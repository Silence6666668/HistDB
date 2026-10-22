from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List

from src.llm.model_client import BaseModelClient
from src.stage3.rewrite_cases import stable_pair_id


NEW_FEATURE_RE = re.compile(
    r"\bNEXT\b|\bFILTER\b|\bLET\b|\bWHEN\b[\s\S]{0,160}\bTHEN\b|"
    r"\bRETURN\s+ALL\b|\bWITH\s+ALL\b|\bCALL\s*\([^)]*\)\s*\{|"
    r"\bFOR\s+[A-Za-z_][A-Za-z0-9_]*\s+IN\b|"
    r":\s*\$\s*(?:\(|any\s*\(|all\s*\()|"
    r"\[[^\]]*\]\s*(?:->|<-|-)\s*\{\s*\d|"
    r"\bSEARCH\b[\s\S]{0,160}\bVECTOR\s+INDEX\b|"
    r"\bFINISH\b",
    re.IGNORECASE,
)
BLOCKED_RE = re.compile(
    r"\bPROFILE\b|\bEXPLAIN\b|\bSHOW\b|\bCALL\s+db\.|\bdb\.|\bapoc\.|\bgds\.|"
    r"\bLOAD\s+CSV\b|file://|\bUSE\s+GRAPH\b",
    re.IGNORECASE,
)
UNSUPPORTED_NEO4J_FUNCTION_RE = re.compile(
    r"\b(?:PROPERTY_EXISTS|toJSON)\s*\(",
    re.IGNORECASE,
)
NONDETERMINISTIC_RE = re.compile(
    r"\b(rand|randomUUID|timestamp)\s*\(|"
    r"\b(datetime|localdatetime|date|time|localtime)\s*\(\s*\)",
    re.IGNORECASE,
)
INTERNAL_ID_RE = re.compile(r"\b(id|elementId)\s*\(", re.IGNORECASE)
WRITE_RE = re.compile(
    r"\b(?:CREATE|MERGE|DELETE|DETACH\s+DELETE|SET|REMOVE|FOREACH|LOAD\s+CSV)\b",
    re.IGNORECASE,
)
LIMIT_RE = re.compile(r"\b(?:LIMIT|SKIP|OFFSET)\b", re.IGNORECASE)
ORDER_RE = re.compile(r"\bORDER\s+BY\b", re.IGNORECASE)
PLACEHOLDER_RE = re.compile(r"\b(?:HOLE_|NODE_LABEL_|REL_TYPE_|RET_EXPR_|CONST_|PARAM_)", re.IGNORECASE)
WITH_NEXT_WITHOUT_RETURN_RE = re.compile(
    r"\bWITH\b(?:(?!\bRETURN\b|\bNEXT\b).)*\bNEXT\b",
    re.IGNORECASE | re.DOTALL,
)
SUBQUERY_YIELD_RE = re.compile(
    r"\bCALL\s*(?:\([^)]*\)\s*)?\{[\s\S]*?\}\s*YIELD\b",
    re.IGNORECASE,
)
CALL_SCOPE_SUBQUERY_RE = re.compile(
    r"\bCALL\s*\((?P<imports>[^)]*)\)\s*\{(?P<body>[\s\S]*?)\}",
    re.IGNORECASE,
)
OPTIONAL_MATCH_FILTER_RE = re.compile(
    r"\bOPTIONAL\s+MATCH\b(?:(?!\b(?:MATCH|WITH|RETURN|CALL|NEXT|UNION|FINISH)\b).)*\bFILTER\b",
    re.IGNORECASE | re.DOTALL,
)
UNWIND_SELF_ALIAS_RE = re.compile(
    r"\bUNWIND\s+([A-Za-z_][A-Za-z0-9_]*)\s+AS\s+\1\b",
    re.IGNORECASE,
)
FOR_SELF_ALIAS_RE = re.compile(
    r"\bFOR\s+([A-Za-z_][A-Za-z0-9_]*)\s+IN\s+\1\b",
    re.IGNORECASE,
)
AGGREGATE_FOR_EXPRESSION_RE = re.compile(
    r"\b(?:sum|avg|min|max|collect)\s*\([\s\S]{0,200}\bFOR\b",
    re.IGNORECASE | re.DOTALL,
)
UNTYPED_VARIABLE_LENGTH_RE = re.compile(
    r"\[[A-Za-z_][A-Za-z0-9_]*\s*\*\s*\d*\s*\.\.\s*\d*\s*\]",
    re.IGNORECASE,
)
TYPED_QUANTIFIED_RELATIONSHIP_RE = re.compile(
    r"\[\s*(?:[A-Za-z_][A-Za-z0-9_]*)?\s*:[^\]]+\]\s*(?:->|<-|-)\s*\{",
    re.IGNORECASE,
)
BARE_DYNAMIC_LABEL_TYPE_RE = re.compile(
    r":\s*\$[A-Za-z_][A-Za-z0-9_]*\b",
    re.IGNORECASE,
)
LET_FOLLOWED_BY_WHERE_RE = re.compile(
    r"\bLET\b(?:(?!\bFILTER\b|\bRETURN\b|\bWITH\b|\bMATCH\b|\bCALL\b|\bNEXT\b|\bUNION\b).)*\bWHERE\b",
    re.IGNORECASE | re.DOTALL,
)
LET_SELF_REDECLARE_RE = re.compile(
    r"\bLET\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\1\b",
    re.IGNORECASE,
)
TYPE_SPECIFIC_LET_BEFORE_GUARD_RE = re.compile(
    r"\bLET\b(?:(?!\bNEXT\b|\bRETURN\b|\bWITH\b|\bMATCH\b|\bCALL\b|\bUNION\b).)*"
    r"(?:size|vector_dimension_count|vector_norm|vector_to_list|vector_distance_[A-Za-z0-9_]+|vector_similarity_[A-Za-z0-9_]+)\s*"
    r"\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:,|\))"
    r"(?:(?!\bNEXT\b|\bRETURN\b|\bWITH\b|\bMATCH\b|\bCALL\b|\bUNION\b).)*"
    r"\bFILTER\s+\1\s+IS\s*::",
    re.IGNORECASE | re.DOTALL,
)
NEXT_REQUIRES_IN_SCOPE_RE = re.compile(r"^\s*(?:WHEN\b|FILTER\b|LET\b|RETURN\b)", re.IGNORECASE)
NEXT_PROPERTY_REF_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\.", re.IGNORECASE)
RETURN_CLAUSE_RE = re.compile(r"\bRETURN\b(?P<body>[\s\S]*)$", re.IGNORECASE)
RETURN_TRAILING_CLAUSE_RE = re.compile(
    r"\b(?:ORDER\s+BY|SKIP|OFFSET|LIMIT|NEXT|UNION|FINISH)\b[\s\S]*$",
    re.IGNORECASE,
)
RETURN_ALIAS_RE = re.compile(r"\bAS\s+([A-Za-z_][A-Za-z0-9_]*)\s*$", re.IGNORECASE)
RETURN_LEADING_DISTINCT_RE = re.compile(r"^\s*DISTINCT\b\s*", re.IGNORECASE)

CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}

DOC_KEYWORDS = [
    "NEXT",
    "FILTER",
    "LET",
    "FOR",
    "WHEN",
    "THEN",
    "RETURN ALL",
    "WITH ALL",
    "CALL (",
    "SEARCH",
    "VECTOR INDEX",
    "FINISH",
    "$any",
    "$all",
    "Cypher 25",
    "conditional query",
    "scope",
]

CONTROLLED_TRANSFORM_RULE_IDS = {
    "with_where_to_filter",
    "match_where_to_filter",
    "with_star_alias_to_let",
    "unwind_to_for",
    "importing_with_to_call_scope",
    "case_to_when_next",
    "guarded_filter_type_predicate",
    "variable_length_to_quantified_relationship",
    "labels_function_to_dynamic_label_predicate",
    "type_function_to_dynamic_type_predicate",
    "vector_procedure_to_search",
    "return_dummy_to_finish",
}

RESULT_EQUIVALENCE_LEVELS = {
    "strong_bag_equivalence",
    "strong_ordered_equivalence",
    "conditional_strong_equivalence",
    "guarded_expression_consistency",
}

RESULT_ORACLE_TYPES = {
    "bag_result_equivalence",
    "ordered_result_equivalence",
    "result_equivalence",
    "guarded_expression_consistency",
    "path_count_or_path_bag_equivalence",
}


def normalize_rule_id(rule_id: Any) -> str:
    out = str(rule_id or "").strip()
    if out.startswith("llm_"):
        out = out.removeprefix("llm_")
    return out


def normalize_additional_rule_ids(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_values = [part.strip() for part in value.split(",")]
    elif isinstance(value, list):
        raw_values = value
    else:
        raw_values = [value]

    out: List[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        rule_id = normalize_rule_id(raw)
        if not rule_id or rule_id in seen:
            continue
        seen.add(rule_id)
        out.append(rule_id)
    return out


def normalize_query_text(query: str) -> str:
    text = re.sub(r"'(?:\\'|''|[^'])*'|\"(?:\\\"|[^\"])*\"", "<str>", query)
    text = re.sub(r"(?<![A-Za-z_])[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", "<num>", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def split_top_level_commas(text: str) -> List[str]:
    items: List[str] = []
    current: List[str] = []
    depth = 0
    quote: str | None = None
    idx = 0
    while idx < len(text):
        ch = text[idx]
        if quote:
            current.append(ch)
            if ch == quote:
                if quote == "'" and idx + 1 < len(text) and text[idx + 1] == "'":
                    current.append(text[idx + 1])
                    idx += 1
                else:
                    quote = None
            idx += 1
            continue
        if ch in {"'", '"'}:
            quote = ch
            current.append(ch)
        elif ch in "([{":
            depth += 1
            current.append(ch)
        elif ch in ")]}":
            depth = max(0, depth - 1)
            current.append(ch)
        elif ch == "," and depth == 0:
            items.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
        idx += 1
    if current:
        items.append("".join(current).strip())
    return items


def visible_names_from_return_segment(segment: str) -> set[str] | None:
    match = RETURN_CLAUSE_RE.search(segment)
    if not match:
        return set()
    body = RETURN_TRAILING_CLAUSE_RE.sub("", match.group("body")).strip()
    if not body:
        return set()

    visible: set[str] = set()
    for item in split_top_level_commas(body):
        if not item:
            continue
        if item == "*":
            return None
        alias_match = RETURN_ALIAS_RE.search(item)
        if alias_match:
            visible.add(alias_match.group(1))
            continue
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item):
            visible.add(item)
    return visible


def final_return_columns(query: str) -> List[str] | None:
    matches = list(re.finditer(r"\bRETURN\b", query, re.IGNORECASE))
    if not matches:
        return None
    body = query[matches[-1].end():]
    body = RETURN_TRAILING_CLAUSE_RE.sub("", body).strip()
    body = RETURN_LEADING_DISTINCT_RE.sub("", body).strip()
    if not body:
        return None

    columns: List[str] = []
    for item in split_top_level_commas(body):
        item = item.strip()
        if not item:
            continue
        if item == "*":
            return None
        alias_match = RETURN_ALIAS_RE.search(item)
        if alias_match:
            columns.append(alias_match.group(1).lower())
        else:
            columns.append(re.sub(r"\s+", " ", item).strip().lower())
    return columns or None


def projection_static_errors(original_query: str, rewritten_query: str) -> List[str]:
    original_columns = final_return_columns(original_query)
    rewritten_columns = final_return_columns(rewritten_query)
    if not original_columns or not rewritten_columns:
        return []
    if original_columns != rewritten_columns:
        return [
            "projected_column_names_changed:"
            + ",".join(original_columns)
            + "->"
            + ",".join(rewritten_columns)
        ]
    return []


def subquery_scope_static_errors(query: str) -> List[str]:
    errors: List[str] = []
    for match in CALL_SCOPE_SUBQUERY_RE.finditer(query):
        imports_text = match.group("imports") or ""
        imports = {
            item.strip()
            for item in split_top_level_commas(imports_text)
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item.strip())
        }
        if not imports:
            continue
        body = match.group("body") or ""
        return_columns = final_return_columns(body)
        if not return_columns:
            continue
        for imported in sorted(imports):
            if imported.lower() in return_columns:
                errors.append(f"subquery_returns_imported_variable:{imported}")
    return errors


def next_scope_static_errors(rewritten_query: str) -> List[str]:
    errors: List[str] = []
    parts = re.split(r"\bNEXT\b", rewritten_query, flags=re.IGNORECASE)
    for previous, current in zip(parts, parts[1:]):
        if not NEXT_REQUIRES_IN_SCOPE_RE.search(current):
            continue
        visible = visible_names_from_return_segment(previous)
        if visible is None:
            continue
        property_refs = sorted(set(NEXT_PROPERTY_REF_RE.findall(current)))
        for ref in property_refs:
            if ref not in visible:
                errors.append(f"next_segment_uses_unreturned_variable:{ref}")
    return errors


def stable_doc_fingerprint(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def load_doc_snippets(path: str | Path | None, max_snippets: int = 8) -> List[Dict[str, Any]]:
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        return []

    scored: List[tuple[int, Dict[str, Any]]] = []
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            haystack = " ".join(
                [
                    str(row.get("page_title") or ""),
                    str(row.get("section_title") or ""),
                    str(row.get("url") or ""),
                    str(row.get("text") or ""),
                    " ".join(str(tag) for tag in row.get("tags", []) or []),
                ]
            )
            score = 0
            lower = haystack.lower()
            for idx, keyword in enumerate(DOC_KEYWORDS):
                if keyword.lower() in lower:
                    score += 20 - min(idx, 10)
            if score <= 0:
                continue
            text = str(row.get("text") or "").strip()
            if not text:
                continue
            snippet = {
                "chunk_id": row.get("chunk_id") or stable_doc_fingerprint(text),
                "url": row.get("url"),
                "page_title": row.get("page_title"),
                "section_title": row.get("section_title"),
                "text": re.sub(r"\s+", " ", text)[:900],
            }
            scored.append((score, snippet))

    scored.sort(key=lambda item: item[0], reverse=True)
    seen: set[str] = set()
    out: List[Dict[str, Any]] = []
    for _, snippet in scored:
        key = str(snippet.get("chunk_id") or "")
        if key in seen:
            continue
        seen.add(key)
        out.append(snippet)
        if len(out) >= max_snippets:
            break
    return out


def format_doc_snippets(snippets: Iterable[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for idx, snippet in enumerate(snippets, start=1):
        title = snippet.get("section_title") or snippet.get("page_title") or snippet.get("chunk_id")
        url = snippet.get("url") or ""
        text = snippet.get("text") or ""
        lines.append(f"[doc {idx}] {title} | {url}\n{text}")
    return "\n\n".join(lines) if lines else "(no snippets loaded)"


def static_rewrite_errors(
    *,
    original_query: str,
    rewritten_query: str,
    allow_write_main: bool = False,
) -> List[str]:
    errors: List[str] = []
    original = str(original_query or "").strip()
    rewritten = str(rewritten_query or "").strip()
    both = f"{original}\n{rewritten}"

    if not original:
        errors.append("empty_original_main_query")
    if not rewritten:
        errors.append("empty_rewritten_main_query")
    if original and rewritten and normalize_query_text(original) == normalize_query_text(rewritten):
        errors.append("rewrite_does_not_change_query")
    if rewritten and not re.match(r"(?is)^\s*CYPHER\s+25\b", rewritten):
        errors.append("rewritten_query_missing_cypher25_prefix")
    if rewritten and not NEW_FEATURE_RE.search(rewritten):
        errors.append("rewritten_query_missing_new_cypher25_feature")
    if BLOCKED_RE.search(both):
        errors.append("blocked_global_admin_extension_or_external_feature")
    if UNSUPPORTED_NEO4J_FUNCTION_RE.search(both):
        errors.append("unsupported_neo4j_function")
    if NONDETERMINISTIC_RE.search(both):
        errors.append("nondeterministic_function")
    if INTERNAL_ID_RE.search(both):
        errors.append("internal_id_dependency")
    if PLACEHOLDER_RE.search(both):
        errors.append("unresolved_placeholder")
    if not allow_write_main and (WRITE_RE.search(original) or WRITE_RE.search(rewritten)):
        errors.append("write_main_query_not_allowed")
    if LIMIT_RE.search(both) and not ORDER_RE.search(both):
        errors.append("limit_skip_or_offset_without_order_by")
    if re.search(r"//|/\*|\*/", both):
        errors.append("cypher_comment")
    if WITH_NEXT_WITHOUT_RETURN_RE.search(rewritten):
        errors.append("with_before_next_without_return_boundary")
    for prefix in re.split(r"\bNEXT\b", rewritten, flags=re.IGNORECASE)[:-1]:
        if not re.search(r"\bRETURN\b", prefix, re.IGNORECASE):
            errors.append("next_without_return_boundary")
            break
        if re.search(r"\b(?:MATCH|OPTIONAL\s+MATCH|UNWIND|WITH)\b(?:(?!\bRETURN\b).)*$", prefix, re.IGNORECASE | re.DOTALL):
            errors.append("next_without_return_boundary")
            break
    if SUBQUERY_YIELD_RE.search(both):
        errors.append("subquery_call_followed_by_yield")
    if OPTIONAL_MATCH_FILTER_RE.search(rewritten):
        errors.append("optional_match_filter_changes_null_preserving_semantics")
    if UNWIND_SELF_ALIAS_RE.search(rewritten):
        errors.append("unwind_self_alias_redeclares_variable")
    if FOR_SELF_ALIAS_RE.search(rewritten):
        errors.append("for_self_alias_redeclares_variable")
    if AGGREGATE_FOR_EXPRESSION_RE.search(rewritten):
        errors.append("aggregate_over_for_expression_invalid")
    if UNTYPED_VARIABLE_LENGTH_RE.search(original) and TYPED_QUANTIFIED_RELATIONSHIP_RE.search(rewritten):
        errors.append("untyped_variable_length_rewritten_to_typed_relationship")
    if BARE_DYNAMIC_LABEL_TYPE_RE.search(rewritten):
        errors.append("bare_dynamic_label_or_type_without_parentheses")
    if LET_FOLLOWED_BY_WHERE_RE.search(rewritten):
        errors.append("let_followed_by_where_use_filter")
    if LET_SELF_REDECLARE_RE.search(rewritten):
        errors.append("let_self_redeclare")
    if TYPE_SPECIFIC_LET_BEFORE_GUARD_RE.search(rewritten):
        errors.append("type_specific_function_before_guard_filter")
    errors.extend(subquery_scope_static_errors(rewritten))
    errors.extend(next_scope_static_errors(rewritten))
    errors.extend(projection_static_errors(original, rewritten))
    return errors


def rewrite_metadata_errors(rewrite: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    rule_id = normalize_rule_id(
        rewrite.get("transform_rule_id")
        or rewrite.get("rewrite_type")
        or ""
    )
    additional_rule_ids = normalize_additional_rule_ids(
        rewrite.get("additional_transform_rule_ids")
        or rewrite.get("transform_rule_ids")
        or []
    )
    equivalence_level = str(rewrite.get("equivalence_level") or "").strip()
    oracle_type = str(rewrite.get("oracle_type") or "").strip()

    if not rule_id:
        errors.append("missing_transform_rule_id")
    elif rule_id not in CONTROLLED_TRANSFORM_RULE_IDS:
        errors.append(f"unknown_transform_rule_id:{rule_id}")

    for additional_rule_id in additional_rule_ids:
        if additional_rule_id == rule_id:
            continue
        if additional_rule_id not in CONTROLLED_TRANSFORM_RULE_IDS:
            errors.append(f"unknown_additional_transform_rule_id:{additional_rule_id}")

    if not equivalence_level:
        errors.append("missing_equivalence_level")
    elif equivalence_level not in RESULT_EQUIVALENCE_LEVELS:
        errors.append(f"unsupported_for_result_selfdiff_equivalence_level:{equivalence_level}")

    if not oracle_type:
        errors.append("missing_oracle_type")
    elif oracle_type not in RESULT_ORACLE_TYPES:
        errors.append(f"unsupported_for_result_selfdiff_oracle_type:{oracle_type}")

    preconditions = rewrite.get("preconditions_checked")
    if not isinstance(preconditions, list) or not preconditions:
        errors.append("missing_preconditions_checked")

    forbidden = rewrite.get("forbidden_contexts_avoided")
    if not isinstance(forbidden, list) or not forbidden:
        errors.append("missing_forbidden_contexts_avoided")

    return errors


def _case_brief(case: Dict[str, Any]) -> str:
    return json.dumps(
        {
            "case_id": case.get("case_id"),
            "query_id": case.get("query_id"),
            "setup_queries": case.get("setup_queries") or [],
            "main_query": case.get("main_query") or "",
            "cleanup_queries": case.get("cleanup_queries") or [],
            "notes": case.get("notes") or "",
            "quality": case.get("quality") or {},
            "generation_profile": case.get("generation_profile") or case.get("quality", {}).get("generation_profile"),
        },
        ensure_ascii=False,
        indent=2,
    )


def build_rewrite_prompt(case: Dict[str, Any], doc_snippets: List[Dict[str, Any]], *, max_rewrites: int) -> str:
    return (
        "Rewrite the following existing test case into an equivalent Neo4j Cypher 25 main query if it is safe.\n\n"
        f"Maximum rewrites requested: {max_rewrites}\n\n"
        "Official/document-grounding snippets:\n"
        f"{format_doc_snippets(doc_snippets)}\n\n"
        "Input testcase:\n"
        f"{_case_brief(case)}\n\n"
        "Remember: preserve setup and cleanup, rewrite only main_query, and return JSON only."
    )


def build_verify_prompt(
    *,
    case: Dict[str, Any],
    rewrite: Dict[str, Any],
    doc_snippets: List[Dict[str, Any]],
) -> str:
    return (
        "Verify this proposed Cypher 25 metamorphic rewrite.\n\n"
        "Official/document-grounding snippets:\n"
        f"{format_doc_snippets(doc_snippets)}\n\n"
        "Original testcase:\n"
        f"{_case_brief(case)}\n\n"
        "Proposed rewrite:\n"
        f"{json.dumps(rewrite, ensure_ascii=False, indent=2)}\n\n"
        "Return JSON only."
    )


def normalize_rewrite_payload(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    status = str(payload.get("status") or "").strip().lower()
    if status == "not_transformable":
        return []
    if status == "generated" and (payload.get("new_query") or payload.get("rewritten_main_query")):
        rule_id = normalize_rule_id(payload.get("transform_rule_id") or payload.get("rewrite_type"))
        return [
            {
                "transform_rule_id": rule_id,
                "rewrite_type": rule_id or "llm_cypher25_rewrite",
                "additional_transform_rule_ids": normalize_additional_rule_ids(
                    payload.get("additional_transform_rule_ids")
                    or payload.get("transform_rule_ids")
                    or []
                ),
                "equivalence_level": payload.get("equivalence_level"),
                "oracle_type": payload.get("oracle_type"),
                "rewritten_main_query": payload.get("new_query") or payload.get("rewritten_main_query"),
                "cypher25_features_used": payload.get("cypher25_features_used") or [],
                "preconditions_checked": payload.get("preconditions_checked") or [],
                "forbidden_contexts_avoided": payload.get("forbidden_contexts_avoided") or [],
                "equivalence_rationale": payload.get("reasoning_summary") or payload.get("reason") or "",
                "safety_conditions": payload.get("safety_conditions") or [],
                "verification_plan": payload.get("verification_plan") or "",
                "positive_controls": payload.get("positive_controls") or [],
                "negative_controls": payload.get("negative_controls") or [],
                "oracle_rule": payload.get("oracle_rule") or {},
                "mutation_axes": payload.get("mutation_axes") or [],
                "minimization_hints": payload.get("minimization_hints") or [],
            }
        ]
    if status and status != "generated":
        return []
    if not payload.get("applicable", False):
        return []
    rewrites = payload.get("rewrites")
    if isinstance(rewrites, list):
        normalized: List[Dict[str, Any]] = []
        for rw in rewrites:
            if not isinstance(rw, dict):
                continue
            rule_id = normalize_rule_id(rw.get("transform_rule_id") or rw.get("rewrite_type"))
            normalized.append(
                {
                    **rw,
                    "transform_rule_id": rule_id,
                    "rewrite_type": rule_id or rw.get("rewrite_type") or "llm_cypher25_rewrite",
                    "additional_transform_rule_ids": normalize_additional_rule_ids(
                        rw.get("additional_transform_rule_ids")
                        or rw.get("transform_rule_ids")
                        or payload.get("additional_transform_rule_ids")
                        or payload.get("transform_rule_ids")
                        or []
                    ),
                    "rewritten_main_query": rw.get("rewritten_main_query") or rw.get("new_query"),
                    "equivalence_level": rw.get("equivalence_level") or payload.get("equivalence_level"),
                    "oracle_type": rw.get("oracle_type") or payload.get("oracle_type"),
                    "preconditions_checked": rw.get("preconditions_checked") or payload.get("preconditions_checked") or [],
                    "forbidden_contexts_avoided": rw.get("forbidden_contexts_avoided") or payload.get("forbidden_contexts_avoided") or [],
                    "positive_controls": rw.get("positive_controls") or payload.get("positive_controls") or [],
                    "negative_controls": rw.get("negative_controls") or payload.get("negative_controls") or [],
                    "oracle_rule": rw.get("oracle_rule") or payload.get("oracle_rule") or {},
                    "mutation_axes": rw.get("mutation_axes") or payload.get("mutation_axes") or [],
                    "minimization_hints": rw.get("minimization_hints") or payload.get("minimization_hints") or [],
                }
            )
        return normalized
    if payload.get("rewritten_main_query"):
        rule_id = normalize_rule_id(payload.get("transform_rule_id") or payload.get("rewrite_type"))
        return [
            {
                "transform_rule_id": rule_id,
                "rewrite_type": rule_id or "llm_cypher25_rewrite",
                "additional_transform_rule_ids": normalize_additional_rule_ids(
                    payload.get("additional_transform_rule_ids")
                    or payload.get("transform_rule_ids")
                    or []
                ),
                "equivalence_level": payload.get("equivalence_level"),
                "oracle_type": payload.get("oracle_type"),
                "rewritten_main_query": payload.get("rewritten_main_query"),
                "cypher25_features_used": payload.get("cypher25_features_used") or [],
                "preconditions_checked": payload.get("preconditions_checked") or [],
                "forbidden_contexts_avoided": payload.get("forbidden_contexts_avoided") or [],
                "equivalence_rationale": payload.get("equivalence_rationale") or payload.get("reason") or "",
                "safety_conditions": payload.get("safety_conditions") or [],
                "verification_plan": payload.get("verification_plan") or "",
                "positive_controls": payload.get("positive_controls") or [],
                "negative_controls": payload.get("negative_controls") or [],
                "oracle_rule": payload.get("oracle_rule") or {},
                "mutation_axes": payload.get("mutation_axes") or [],
                "minimization_hints": payload.get("minimization_hints") or [],
            }
        ]
    return []


def verifier_accepts(
    payload: Dict[str, Any],
    *,
    min_confidence: str = "high",
) -> bool:
    if payload.get("equivalent") is not True:
        return False
    confidence = str(payload.get("confidence") or "low").strip().lower()
    return CONFIDENCE_RANK.get(confidence, 0) >= CONFIDENCE_RANK.get(min_confidence, 2)


def summarize_rejections(rows: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        reason = str(row.get("reject_reason") or "unknown")
        counts[reason] += 1
        for error in row.get("static_errors", []) or []:
            counts[f"static:{error}"] += 1
    return dict(sorted(counts.items()))


def make_pair_record(
    *,
    case: Dict[str, Any],
    rewrite: Dict[str, Any],
    verifier_payload: Dict[str, Any] | None,
    rewrite_profile: str,
) -> Dict[str, Any]:
    original_query = str(case.get("main_query") or "").strip()
    rewritten_query = str(rewrite.get("rewritten_main_query") or "").strip()
    rewrite_type = str(rewrite.get("rewrite_type") or "llm_cypher25_rewrite").strip()
    if not rewrite_type.startswith("llm_"):
        rewrite_type = f"llm_{rewrite_type}"
    pair_id = stable_pair_id(str(case.get("case_id") or ""), rewrite_type, rewritten_query)
    return {
        "pair_id": pair_id,
        "rewrite_type": rewrite_type,
        "original_case_id": case.get("case_id"),
        "original_query_id": case.get("query_id"),
        "original_main_query": original_query,
        "rewritten_main_query": rewritten_query,
        "setup_queries": case.get("setup_queries", []),
        "cleanup_queries": case.get("cleanup_queries", []),
        "source_id": case.get("source_id"),
        "skeleton_id": case.get("skeleton_id"),
        "generation_strategy": case.get("generation_strategy"),
        "rewrite_profile": rewrite_profile,
        "transform_rule_id": rewrite.get("transform_rule_id") or rewrite.get("rewrite_type"),
        "additional_transform_rule_ids": normalize_additional_rule_ids(
            rewrite.get("additional_transform_rule_ids")
            or rewrite.get("transform_rule_ids")
            or []
        ),
        "equivalence_level": rewrite.get("equivalence_level"),
        "oracle_type": rewrite.get("oracle_type"),
        "cypher25_features_used": rewrite.get("cypher25_features_used") or [],
        "preconditions_checked": rewrite.get("preconditions_checked") or [],
        "forbidden_contexts_avoided": rewrite.get("forbidden_contexts_avoided") or [],
        "equivalence_rationale": rewrite.get("equivalence_rationale") or "",
        "safety_conditions": rewrite.get("safety_conditions") or [],
        "verification_plan": rewrite.get("verification_plan") or "",
        "positive_controls": rewrite.get("positive_controls") or [],
        "negative_controls": rewrite.get("negative_controls") or [],
        "oracle_rule": rewrite.get("oracle_rule") or {},
        "mutation_axes": rewrite.get("mutation_axes") or [],
        "minimization_hints": rewrite.get("minimization_hints") or [],
        "llm_verification": verifier_payload or {},
    }
