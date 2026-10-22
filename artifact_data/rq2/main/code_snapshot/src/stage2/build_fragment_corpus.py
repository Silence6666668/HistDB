from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Tuple

from .common import clean_text, first_nonempty, stable_id
from .fragment_policy import (
    documentation_fragment_text, filter_legacy_neo4j_index_fragments, is_documented_invalid_example,
)

CLAUSE_ORDER = [
    "OPTIONAL MATCH", "MATCH", "UNWIND", "WITH", "WHERE", "CALL", "CREATE", "MERGE",
    "SET", "DELETE", "REMOVE", "RETURN", "ORDER BY", "SKIP", "LIMIT", "UNION",
]
CLAUSE_RE = re.compile(
    r"\b(OPTIONAL\s+MATCH|MATCH|UNWIND|WITH|WHERE|CALL|CREATE|MERGE|SET|DELETE|REMOVE|RETURN|ORDER\s+BY|SKIP|LIMIT|UNION)\b",
    re.IGNORECASE,
)


def classify_group_from_text(text: str, tags: List[str] | None = None) -> str:
    low = (text or "").lower()
    tags = [t.lower() for t in (tags or [])]
    if "order by" in low or "limit" in low or "skip" in low:
        return "ordering"
    if any(k in low for k in ["count(", "sum(", "avg(", "min(", "max(", "collect(", "nodes("]):
        return "aggregation"
    if any(k in low for k in ["all(", "any(", "none(", "single(", " where "]):
        return "predicate"
    if any(k in low for k in ["*", "cycle", "path", "nodes(p)", "relationships("]):
        return "structure"
    if any(k in low for k in ["create ", "merge ", "set ", "delete ", "remove "]):
        return "update"
    if "feature" in tags:
        return "feature"
    return "other"


def split_clauses(query_text: str) -> List[Tuple[str, str]]:
    text = clean_text(query_text)
    if not text:
        return []
    matches = list(CLAUSE_RE.finditer(text))
    if not matches:
        return [("FULL_QUERY", text)]
    out: List[Tuple[str, str]] = []
    for idx, m in enumerate(matches):
        start = m.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        clause_name = m.group(1).upper().replace("  ", " ")
        snippet = text[start:end].strip()
        out.append((clause_name, snippet))
    return out


def _normalize_feature_text(feature_record: Dict[str, Any]) -> str | None:
    return first_nonempty(
        feature_record.get("feature_text"),
        feature_record.get("text"),
        feature_record.get("feature_name"),
        feature_record.get("feature_description"),
    )


def build_fragment_corpus(
    skeleton_records: Iterable[Dict[str, Any]],
    feature_records: Iterable[Dict[str, Any]],
    extra_fragments: Iterable[Dict[str, Any]] | None = None,
) -> List[Dict[str, Any]]:
    fragments: List[Dict[str, Any]] = []
    seen_keys: set[tuple[str, str, str]] = set()

    for record in skeleton_records:
        query_id = str(record.get("query_id") or "unknown")
        source_id = str(record.get("source_id") or "unknown")
        query_text = clean_text(first_nonempty(record.get("query_text"), record.get("query_instance"), "") or "")
        skeleton_text = clean_text(str(record.get("skeleton_text") or ""))
        if query_text:
            key = (query_id, "historical_query_full", query_text)
            if key not in seen_keys:
                seen_keys.add(key)
                fragments.append({
                    "fragment_id": stable_id("frag", query_id, "full", query_text),
                    "query_id": query_id,
                    "source_id": source_id,
                    "source_type": "historical_query_full",
                    "group_hint": classify_group_from_text(query_text),
                    "text": query_text,
                    "tags": ["full_query"],
                    "metadata": {"repo": record.get("repo"), "issue_number": record.get("issue_number"), "url": record.get("url")},
                })
            for clause_name, snippet in split_clauses(query_text):
                key = (query_id, clause_name, snippet)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                fragments.append({
                    "fragment_id": stable_id("frag", query_id, clause_name, snippet),
                    "query_id": query_id,
                    "source_id": source_id,
                    "source_type": "historical_query_clause",
                    "group_hint": classify_group_from_text(snippet, [clause_name]),
                    "text": snippet,
                    "tags": [clause_name.lower().replace(" ", "_")],
                    "metadata": {"repo": record.get("repo"), "issue_number": record.get("issue_number"), "url": record.get("url")},
                })
        if skeleton_text:
            key = (query_id, "skeleton", skeleton_text)
            if key not in seen_keys:
                seen_keys.add(key)
                fragments.append({
                    "fragment_id": stable_id("frag", query_id, "skeleton", skeleton_text),
                    "query_id": query_id,
                    "source_id": source_id,
                    "source_type": "skeleton",
                    "group_hint": "structure",
                    "text": skeleton_text,
                    "tags": ["skeleton"],
                    "metadata": {"repo": record.get("repo"), "issue_number": record.get("issue_number"), "url": record.get("url")},
                })

    for record in feature_records:
        query_id = str(record.get("query_id") or "unknown")
        source_id = str(record.get("source_id") or "unknown")
        feature_text = clean_text(_normalize_feature_text(record) or "")
        evidence = clean_text(first_nonempty(record.get("evidence"), record.get("query_instance"), "") or "")
        tags = [str(t).strip() for t in record.get("tags", []) if str(t).strip()]
        if feature_text:
            key = (query_id, "feature", feature_text)
            if key not in seen_keys:
                seen_keys.add(key)
                fragments.append({
                    "fragment_id": stable_id("frag", query_id, "feature", feature_text),
                    "query_id": query_id,
                    "source_id": source_id,
                    "source_type": "feature",
                    "group_hint": classify_group_from_text(feature_text, ["feature"] + tags),
                    "text": feature_text,
                    "tags": ["feature"] + tags[:8],
                    "metadata": {"repo": record.get("repo"), "issue_number": record.get("issue_number"), "url": record.get("url")},
                })
        if evidence:
            key = (query_id, "feature_evidence", evidence)
            if key not in seen_keys:
                seen_keys.add(key)
                fragments.append({
                    "fragment_id": stable_id("frag", query_id, "feature_evidence", evidence),
                    "query_id": query_id,
                    "source_id": source_id,
                    "source_type": "feature_evidence",
                    "group_hint": classify_group_from_text(evidence, tags),
                    "text": evidence,
                    "tags": ["feature_evidence"] + tags[:8],
                    "metadata": {"repo": record.get("repo"), "issue_number": record.get("issue_number"), "url": record.get("url")},
                })

    for frag in extra_fragments or []:
        # Inspect narrative before clean_text removes code-fence boundaries.
        if is_documented_invalid_example(frag):
            continue
        text = clean_text(documentation_fragment_text(frag))
        if not text:
            continue
        key = (str(frag.get("query_id") or "external"), str(frag.get("source_type") or "external"), text)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        source_id = str(frag.get("source_id") or frag.get("source") or "external")
        source_type = str(
            frag.get("source_type")
            or ("official_chunk" if (frag.get("source") or frag.get("url")) else "external")
        )
        metadata = dict(frag.get("metadata", {}) or {})
        for key_name in ("source", "url", "page_title", "section_title", "section_path", "version", "chunk_type"):
            if key_name in frag and key_name not in metadata:
                metadata[key_name] = frag.get(key_name)
        fragments.append({
            "fragment_id": str(frag.get("fragment_id") or stable_id("frag", *key)),
            "query_id": str(frag.get("query_id") or "external"),
            "source_id": source_id,
            "source_type": source_type,
            "group_hint": str(frag.get("group_hint") or classify_group_from_text(text, list(frag.get("tags", [])))),
            "text": text,
            "tags": [str(t) for t in frag.get("tags", [])][:12],
            "metadata": metadata,
        })

    filtered, _ = filter_legacy_neo4j_index_fragments(fragments)
    return filtered
