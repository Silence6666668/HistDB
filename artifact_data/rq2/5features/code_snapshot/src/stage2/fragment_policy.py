from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from .common import clean_text


LEGACY_NEO4J_INDEX_RE = re.compile(
    r"\b(?:CREATE|DROP)\s+(?:BTREE\s+)?INDEX\s+ON\s*:\s*[A-Za-z_`]",
    re.IGNORECASE,
)

HISTORICAL_FRAGMENT_TYPES = {
    "historical_query_full", "historical_query_clause", "feature_evidence", "feature", "skeleton",
}
DOCUMENTED_ERROR_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        # "The above query would work if ..." also precedes the corrected
        # WHERE example, so require the explicit inaccessible RETURN warning.
        r"\b(?:succeeding|subsequent)\s+RETURN\s+no\s+longer\s+has\s+access\b",
        r"\b(?:(?:above|following|below|this)\s+query|example\s+(?:above|below)|query)\s+"
        r"(?:(?:will|would)\s+(?:throw|raise)|throws|raises)\s+"
        r"(?:an?\s+)?(?:error|exception)\s*(?=[:.!]|since\b|because\b|$)",
        r"\b(?:above|following|below|this)\s+query\s+"
        r"(?:is\s+(?:invalid|not\s+valid)|does\s+not\s+work)\b",
        r"\b(?:the|this|above|following|below)\s+query\s+(?:therefore\s+)?"
        r"fails\s*(?=[:.!]|since\b|because\b|$)",
        r"\b[0-9]{2}[A-Z][0-9]{2}\s*:\s*error\b",
        r"\bquery\s+parser\s+will\s+return\s+an?\s+error\s+because\b",
        r"\b(?:below|following)\s+(?:two\s+)?queries\s+(?:would\s+)?"
        r"(?:consequently\s+)?return\s+(?:the\s+)?same\s+error\s+message\b",
    )
]
FENCED_CODE_RE = re.compile(r"```.*?```|~~~.*?~~~", re.DOTALL)
QUOTED_TEXT_RE = re.compile(
    r"(?<!\w)(?:'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|`[^`]*`)"
)


def documentation_fragment_text(fragment: Dict[str, Any]) -> str:
    """Preserve the complete documented example when converting raw chunks."""
    text = str(fragment.get("text") or fragment.get("embedding_text") or "")
    parts = [text]
    for block in fragment.get("code_blocks", []) or []:
        if not isinstance(block, dict):
            continue
        code = str(block.get("code") or "").strip()
        if code:
            lang = str(block.get("lang") or "").strip()
            parts.append(f"```{lang}\n{code}\n```")
    return "\n\n".join(parts).strip()


def documentation_body_fingerprint(fragment: Dict[str, Any]) -> str:
    """Match reviewed content across raw chunks, RAG wrappers and old lexical IDs."""
    text = documentation_fragment_text(fragment)
    body = re.sub(r"(?s)^\[chunk_type\].*?\[text\]\s*", "", text)
    normalized = clean_text(body)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@lru_cache(maxsize=1)
def _reviewed_documentation_decisions() -> Dict[str, bool]:
    path = Path(__file__).with_name("documentation_example_review.json")
    policy = json.loads(path.read_text(encoding="utf-8"))
    return {entry["body_sha256"]: entry["keep"] for entry in policy["decisions"]}


def is_documented_invalid_example(
    fragment: Dict[str, Any], *, assume_official: bool = False,
) -> bool:
    """Recognize explicit counterexamples in docs, not historical bug evidence.

    Official provenance is required, but chunk labels are not trusted: scraped
    definitions can also contain negative queries. Only narrative error markers
    are inspected, so valid null/empty-list behavior and strings
    returned by example queries are not mistaken for rejected examples.
    """
    source_type = str(fragment.get("source_type") or "").lower()
    if source_type in HISTORICAL_FRAGMENT_TYPES:
        return False
    metadata = fragment.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    text = str(fragment.get("text") or fragment.get("embedding_text") or "")

    provenance = " ".join(str(value or "") for value in (
        source_type, fragment.get("source_id"), fragment.get("source"), fragment.get("url"),
        metadata.get("source"), metadata.get("url"),
    )).lower()
    official = (
        assume_official or source_type.startswith("official_")
        or any(marker in provenance for marker in (
            "cypher_manual", "neo4j_manual", "neo4j_docs", "neo4j.com/docs",
            "memgraph_querying", "memgraph_manual", "memgraph.com/docs",
        ))
        or bool(re.search(r"\[source\]\s*(?:cypher_manual|memgraph_querying)\b", text, re.IGNORECASE))
    )
    if not official:
        return False

    # Content hashes, not IDs alone: reviewed valid neighbors override shared
    # error prose, while markerless counterexamples cannot leak from old caches.
    keep = _reviewed_documentation_decisions().get(documentation_body_fingerprint(fragment))
    if keep is not None:
        return not keep

    narrative = FENCED_CODE_RE.sub(" ", text)
    narrative = QUOTED_TEXT_RE.sub(" ", narrative)
    return any(pattern.search(narrative) for pattern in DOCUMENTED_ERROR_PATTERNS)


def has_legacy_neo4j_index_syntax(text: str) -> bool:
    return bool(LEGACY_NEO4J_INDEX_RE.search(text or ""))


def filter_legacy_neo4j_index_fragments(
    fragments: Iterable[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    kept: List[Dict[str, Any]] = []
    reasons: Counter[str] = Counter()
    for fragment in fragments:
        if has_legacy_neo4j_index_syntax(str(fragment.get("text") or "")):
            reasons["legacy_neo4j_index_syntax"] += 1
            continue
        kept.append(fragment)
    return kept, dict(reasons)


def filter_legacy_neo4j_index_groups(
    groups: Iterable[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    kept_groups: List[Dict[str, Any]] = []
    reasons: Counter[str] = Counter()
    for group in groups:
        kept_items, item_reasons = filter_legacy_neo4j_index_fragments(
            item for item in (group.get("items") or []) if isinstance(item, dict)
        )
        reasons.update(item_reasons)
        if kept_items:
            kept_group = dict(group)
            kept_group["items"] = kept_items
            kept_groups.append(kept_group)
    return kept_groups, dict(reasons)
