from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any, Dict, Iterable, List, MutableSet

from src.stage2.fragment_policy import has_legacy_neo4j_index_syntax, is_documented_invalid_example


GENERATION_PROFILES = {
    "portable_crossdb",
    "neo4j_semantic",
    "neo4j_cypher25",
    "neo4j_cypher25_metamorphic",
    "neo4j_internal_tier",
    "neo4j_engine_stress",
}
DIVERSITY_PROFILES = {"balanced", "semantic_edges", "boundary_heavy", "engine_stress"}


STRING_RE = re.compile(r"'(?:\\'|''|[^'])*'|\"(?:\\\"|[^\"])*\"")
NUMBER_RE = re.compile(r"(?<![A-Za-z_])[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")
WS_RE = re.compile(r"\s+")

NONDETERMINISTIC_RE = re.compile(
    r"\b(rand|randomUUID|timestamp)\s*\(|\b(datetime|localdatetime|date|time|localtime)\s*\(\s*\)",
    re.IGNORECASE,
)
GLOBAL_STATE_RE = re.compile(
    r"\bSHOW\b|\bCALL\s+db\.|\bdb\.|\bCREATE\s+(INDEX|CONSTRAINT|DATABASE|USER|ROLE)\b|"
    r"\bDROP\s+(INDEX|CONSTRAINT|DATABASE|USER|ROLE)\b",
    re.IGNORECASE,
)
EXTENSION_RE = re.compile(r"\b(apoc|gds|genai)\.|\bLOAD\s+CSV\b|file://", re.IGNORECASE)
INTERNAL_ID_RE = re.compile(r"\b(id|elementId)\s*\(", re.IGNORECASE)
LIMIT_RE = re.compile(r"\bLIMIT\b", re.IGNORECASE)
ORDER_RE = re.compile(r"\bORDER\s+BY\b", re.IGNORECASE)
DEPRECATED_CALL_IMPORT_RE = re.compile(r"\bCALL\s*\{\s*WITH\b", re.IGNORECASE)
PORTABLE_FORBIDDEN_RE = re.compile(
    r"\bCYPHER\s+(?:5|25)\b|\bNEXT\b|\bLET\b|\bFILTER\b|\bIS\s*::|"
    r"\bCALL\s*\([^)]*\)\s*\{|\bvector(?:_|\s*\()|\bVECTOR\b|\bSEARCH\b|"
    r"\bWHEN\b\s+.+\bTHEN\b|\bFOR\s+[A-Za-z_][A-Za-z0-9_]*\s+IN\b|"
    r"\bRETURN\s+ALL\b|\bWITH\s+ALL\b|\bREPEATABLE\s+ELEMENTS\b|"
    r"\bDIFFERENT\s+RELATIONSHIPS\b|\bACYCLIC\b|\bIS\s+(?:NOT\s+)?LABELED\b|"
    r"\bPROPERTY_EXISTS\s*\(|\bcoll\.|\bstring\.|\bformat\s*\(|\ballReduce\s*\(|"
    r"\bduration_between\s*\(|\bpath_length\s*\(|\bcollect_list\s*\(|"
    r"\bUSE\s+GRAPH\b|\bgraph\.byElementId\b",
    re.IGNORECASE | re.DOTALL,
)
CYPHER25_REMOVED_OR_DEPRECATED_RE = re.compile(
    r"\bSET\s+[A-Za-z_][A-Za-z0-9_]*\s*\+?=\s*(?!properties\s*\()[A-Za-z_][A-Za-z0-9_]*\b(?!\s*\()|"
    r"\bMERGE\b[\s\S]{0,240}\{[^{}]*:\s*[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*[^{}]*\}|"
    r"\bindexProvider\b|\bseedCredentials\b|\bexistingDataSeedInstance\b|"
    r"\bdb\.create\.setVectorProperty\s*\(|\bdb\.index\.vector\.createNodeIndex\s*\(|"
    r"\bdb\.index\.vector\.query(?:Nodes|Relationships)\s*\(",
    re.IGNORECASE,
)
UNSUPPORTED_NULLABLE_TYPE_PREDICATE_RE = re.compile(r"\bIS\s*::[^\n\r;]*\?", re.IGNORECASE)
WITH_NEXT_CONDITIONAL_SCOPE_RE = re.compile(r"\bWITH\b[\s\S]{0,300}\bNEXT\s+WHEN\b", re.IGNORECASE)
ELSE_WHEN_CONDITIONAL_RE = re.compile(r"\bELSE\s+WHEN\b", re.IGNORECASE)
LET_SELF_REDECLARE_RE = re.compile(r"\bLET\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\1\b", re.IGNORECASE)
WRITE_CONTEXT_RE = re.compile(r"\b(CREATE|MERGE|SET)\b", re.IGNORECASE)
WRITE_CLAUSE_RE = re.compile(
    r"\b(?:CREATE|MERGE|SET)\b(?P<body>[\s\S]*?)(?=\b(?:MATCH|OPTIONAL\s+MATCH|WITH|RETURN|CALL|UNWIND|DELETE|DETACH|REMOVE|CREATE|MERGE|SET)\b|$)",
    re.IGNORECASE,
)
NESTED_MAP_PROPERTY_VALUE_RE = re.compile(r":\s*(?:\{|\[[^\]]*\{)", re.IGNORECASE | re.DOTALL)
SET_PROPERTY_TO_MAP_RE = re.compile(
    r"\bSET\s+[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*|\[[^\]]+\])\s*=\s*(?:\{|\[[^\]]*\{)",
    re.IGNORECASE | re.DOTALL,
)
CALL_SUBQUERY_RE = re.compile(r"\bCALL\s*(?:\([^)]*\))?\s*\{(?P<body>[\s\S]*?)\}", re.IGNORECASE)
WITH_IMPORT_RE = re.compile(r"\bWITH\s+(?P<vars>[^{}]*?)(?=\b(?:MATCH|OPTIONAL\s+MATCH|WITH|RETURN|CALL|UNWIND|CREATE|MERGE|DELETE|SET|WHERE)\b|$)", re.IGNORECASE)
RETURN_CLAUSE_RE = re.compile(
    r"\bRETURN\s+(?P<items>[\s\S]*?)(?=\b(?:ORDER\s+BY|SKIP|LIMIT|UNION|WITH|MATCH|OPTIONAL\s+MATCH|CALL)\b|\s*$)",
    re.IGNORECASE,
)
IDENT_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")
NEXT_WHEN_RE = re.compile(r"\bNEXT\s+WHEN\b", re.IGNORECASE)
AS_ALIAS_RE = re.compile(r"\bAS\s+([A-Za-z_][A-Za-z0-9_]*)\b", re.IGNORECASE)
DOTTED_VARIABLE_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\.", re.IGNORECASE)
RETURN_EXPORT_RE = re.compile(r"\bRETURN\s+(?:ALL\s+)?(?P<items>[\s\S]*)$", re.IGNORECASE)
COMMON_DOTTED_NAMESPACES = {"db", "graph", "vector", "coll", "string", "point", "duration"}


TAG_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("optional_match", re.compile(r"\bOPTIONAL\s+MATCH\b", re.IGNORECASE)),
    ("call_subquery", re.compile(r"\bCALL\b\s*(?:\([^)]*\))?\s*\{", re.IGNORECASE)),
    ("call_scope", re.compile(r"\bCALL\s*\([^)]*\)\s*\{", re.IGNORECASE)),
    ("conditional_query", re.compile(r"\bWHEN\b[\s\S]{0,200}\bTHEN\b", re.IGNORECASE)),
    ("next_clause", re.compile(r"\bNEXT\b", re.IGNORECASE)),
    ("let_clause", re.compile(r"\bLET\b", re.IGNORECASE)),
    ("filter_clause", re.compile(r"\bFILTER\b", re.IGNORECASE)),
    ("all_quantifier", re.compile(r"\b(?:RETURN|WITH)\s+ALL\b", re.IGNORECASE)),
    ("for_clause", re.compile(r"\bFOR\s+[A-Za-z_][A-Za-z0-9_]*\s+IN\b", re.IGNORECASE)),
    ("type_predicate", re.compile(r"\bIS\s*::", re.IGNORECASE)),
    ("label_predicate", re.compile(r"\bIS\s+(?:NOT\s+)?LABELED\b", re.IGNORECASE)),
    ("property_exists", re.compile(r"\bPROPERTY_EXISTS\s*\(", re.IGNORECASE)),
    ("vector", re.compile(r"\bvector(?:_|\s*\()", re.IGNORECASE)),
    ("path", re.compile(r"\[[^\]]*\*|\bACYCLIC\b|\bTRAIL\b|\bWALK\b|\bSHORTEST\b|\bREPEATABLE\s+ELEMENTS\b|\bDIFFERENT\s+RELATIONSHIPS\b", re.IGNORECASE)),
    ("aggregation", re.compile(r"\b(count|collect|sum|avg|min|max|percentile)\s*\(", re.IGNORECASE)),
    ("expression_subquery", re.compile(r"\b(COUNT|COLLECT|EXISTS)\s*\{", re.IGNORECASE)),
    ("union", re.compile(r"\bUNION\b", re.IGNORECASE)),
    ("write", re.compile(r"\b(CREATE|MERGE|SET|DELETE|DETACH|REMOVE)\b", re.IGNORECASE)),
    ("schema_ddl", re.compile(r"\b(?:CREATE|DROP)\s+(?:TEXT\s+|POINT\s+|VECTOR\s+|LOOKUP\s+|RANGE\s+|FULLTEXT\s+)?(?:CONSTRAINT|INDEX)\b", re.IGNORECASE)),
    ("relationship_constraint", re.compile(r"FOR\s*\(\)\s*-\s*\[[^\]]*\]\s*-\s*\(\)\s*REQUIRE", re.IGNORECASE)),
    ("path_binding_write", re.compile(r"\b(?:MERGE|CREATE)\s+[A-Za-z_][A-Za-z0-9_]*\s*=", re.IGNORECASE)),
    ("merge_on_create_match", re.compile(r"\bMERGE\b[\s\S]{0,600}\bON\s+(?:CREATE|MATCH)\s+SET\b", re.IGNORECASE)),
    ("null_entity_side_effect", re.compile(r"\b(?:SET|REMOVE|DELETE|DETACH\s+DELETE)\s+null\b|\b(?:startNode|endNode)\s*\(\s*null\s*\)|\bnull\s*\.", re.IGNORECASE)),
    ("extreme_literal", re.compile(r"9223372036854775807|-9223372036854775808|1\.7976931348623157E308|4\.9E-324|e[+-]\d{2,}", re.IGNORECASE)),
    ("multi_label_node", re.compile(r"\([^\)\{\]]*:[A-Za-z_][A-Za-z0-9_]*:[A-Za-z_][A-Za-z0-9_]*", re.IGNORECASE)),
    ("order_limit", re.compile(r"\bORDER\s+BY\b|\bLIMIT\b|\bSKIP\b|\bOFFSET\b", re.IGNORECASE)),
    ("map_projection", re.compile(r"\{\s*\.|\bRETURN\b[\s\S]{0,160}\{", re.IGNORECASE)),
    ("temporal", re.compile(r"\b(date|time|datetime|duration)\s*\(", re.IGNORECASE)),
    ("search", re.compile(r"\bSEARCH\b|vector\.query", re.IGNORECASE)),
    ("collection_function", re.compile(r"\bcoll\.", re.IGNORECASE)),
    ("string_function", re.compile(r"\bstring\.", re.IGNORECASE)),
    ("cypher25_function", re.compile(r"\b(format|allReduce|duration_between|path_length|collect_list)\s*\(", re.IGNORECASE)),
]


def normalize_generation_profile(profile: str | None) -> str:
    value = str(profile or "portable_crossdb").strip().lower()
    if value not in GENERATION_PROFILES:
        raise ValueError(f"Unsupported generation profile: {profile}")
    return value


def _split_return_items(items: str) -> List[str]:
    parts: List[str] = []
    current: List[str] = []
    depth = 0
    quote: str | None = None
    i = 0
    while i < len(items):
        ch = items[i]
        if quote:
            current.append(ch)
            if ch == quote:
                if quote == "'" and i + 1 < len(items) and items[i + 1] == "'":
                    current.append(items[i + 1])
                    i += 1
                else:
                    quote = None
            i += 1
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
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
        i += 1
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts


def _return_exports_before_next_when(query: str, next_start: int) -> set[str]:
    prefix = query[:next_start]
    match = RETURN_EXPORT_RE.search(prefix)
    if not match:
        return set()
    exports: set[str] = set()
    for item in _split_return_items(match.group("items")):
        item = item.strip()
        if not item:
            continue
        alias = AS_ALIAS_RE.search(item)
        if alias:
            exports.add(alias.group(1))
            continue
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item):
            exports.add(item)
    return exports


def conditional_next_references_unreturned_dotted_variable(query: str) -> bool:
    text = STRING_RE.sub("''", query)
    for match in NEXT_WHEN_RE.finditer(text):
        exports = _return_exports_before_next_when(text, match.start())
        next_match = re.search(r"\bNEXT\b", text[match.end() :], flags=re.IGNORECASE)
        end = match.end() + next_match.start() if next_match else len(text)
        body = text[match.end() : end]
        for dotted in DOTTED_VARIABLE_RE.finditer(body):
            root = dotted.group(1)
            if root.lower() in COMMON_DOTTED_NAMESPACES:
                continue
            if root not in exports:
                return True
    return False


def normalize_diversity_profile(profile: str | None) -> str:
    value = str(profile or "balanced").strip().lower()
    if value not in DIVERSITY_PROFILES:
        raise ValueError(f"Unsupported diversity profile: {profile}")
    return value


def combined_query_text(case: Dict[str, Any]) -> str:
    return "\n".join(
        [str(query) for query in case.get("setup_queries", []) or []]
        + [str(case.get("main_query") or "")]
        + [str(query) for query in case.get("cleanup_queries", []) or []]
    )


def case_query_texts(case: Dict[str, Any]) -> List[str]:
    return (
        [str(query) for query in case.get("setup_queries", []) or []]
        + [str(case.get("main_query") or "")]
        + [str(query) for query in case.get("cleanup_queries", []) or []]
    )


def has_non_property_value_map_assignment(query: str) -> bool:
    """Return True when a write tries to store a map/list-of-map as a property.

    Neo4j properties can be primitives or arrays of primitives, but not maps.
    This intentionally targets write clauses only so normal RETURN {map: ...}
    expressions remain available for semantic tests.
    """
    text = str(query or "")
    if not WRITE_CONTEXT_RE.search(text):
        return False
    if SET_PROPERTY_TO_MAP_RE.search(text):
        return True
    for match in WRITE_CLAUSE_RE.finditer(text):
        body = match.group("body") or ""
        if NESTED_MAP_PROPERTY_VALUE_RE.search(body):
            return True
    return False


def _split_projection_items(text: str) -> List[str]:
    items: List[str] = []
    current: List[str] = []
    depth = 0
    quote: str | None = None
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            current.append(ch)
            if ch == quote:
                if quote == "'" and i + 1 < len(text) and text[i + 1] == "'":
                    current.append(text[i + 1])
                    i += 1
                else:
                    quote = None
            i += 1
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
            item = "".join(current).strip()
            if item:
                items.append(item)
            current = []
        else:
            current.append(ch)
        i += 1
    item = "".join(current).strip()
    if item:
        items.append(item)
    return items


def _bare_projection_name(item: str) -> str | None:
    raw = str(item or "").strip()
    if not raw or re.search(r"\bAS\b", raw, re.IGNORECASE):
        return None
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", raw):
        return raw
    return None


def has_call_subquery_returning_imported_names(query: str) -> bool:
    """Detect CALL subqueries that return imported variables unchanged.

    Neo4j does not allow a subquery to return a variable name that is already
    present in the outer scope. Returning imported names unchanged is therefore
    normal SyntaxError noise, not a useful bug candidate.
    """
    text = str(query or "")
    for call in CALL_SUBQUERY_RE.finditer(text):
        body = call.group("body") or ""
        with_match = WITH_IMPORT_RE.search(body)
        return_match = RETURN_CLAUSE_RE.search(body)
        if not with_match or not return_match:
            continue
        imported = set()
        for item in _split_projection_items(with_match.group("vars") or ""):
            name = _bare_projection_name(item)
            if name:
                imported.add(name)
        if not imported:
            continue
        for item in _split_projection_items(return_match.group("items") or ""):
            name = _bare_projection_name(item)
            if name and name in imported:
                return True
    return False


def query_fingerprint(case: Dict[str, Any]) -> str:
    text = combined_query_text(case)
    text = STRING_RE.sub("<str>", text)
    text = NUMBER_RE.sub("<num>", text)
    text = WS_RE.sub(" ", text).strip().lower()
    return text


def _record_has_official_allowed_source(source_record: Dict[str, Any]) -> bool:
    allowed = source_record.get("official_filter_allowed_sources") or []
    allowed_text = " ".join(str(value or "") for value in allowed).lower()
    return any(token in allowed_text for token in ("official", "cypher_manual", "neo4j_manual", "neo4j_docs"))


def _fragment_is_official(item: Any, *, source_record: Dict[str, Any] | None = None) -> bool:
    if not isinstance(item, dict):
        return False
    source_type = str(item.get("source_type") or "")
    source_id = str(item.get("source_id") or item.get("source") or "")
    metadata = item.get("metadata")
    metadata_text = ""
    if isinstance(metadata, dict):
        metadata_text = json.dumps(metadata, ensure_ascii=False)
    text = " ".join([source_type, source_id, metadata_text, str(item.get("url") or "")]).lower()
    if source_record is not None and _record_has_official_allowed_source(source_record):
        if source_type.lower() == "external" or source_id.lower() == "external":
            return True
    return "official" in text or "cypher_manual" in text or "neo4j.com/docs" in text


def has_official_doc_grounding(source_record: Dict[str, Any]) -> bool:
    for group in source_record.get("fragment_groups", []) or []:
        if not isinstance(group, dict):
            continue
        if any(
            _fragment_is_official(item, source_record=source_record)
            and not is_documented_invalid_example(item, assume_official=True)
            for item in group.get("items", []) or []
        ):
            return True
    return False


def extract_quality_tags(case: Dict[str, Any]) -> List[str]:
    text = combined_query_text(case)
    return [tag for tag, pattern in TAG_PATTERNS if pattern.search(text)]


def quality_gate_errors(
    case: Dict[str, Any],
    source_record: Dict[str, Any],
    *,
    generation_profile: str,
    diversity_profile: str,
    require_doc_grounding: bool,
    seen_fingerprints: MutableSet[str] | None = None,
) -> List[str]:
    active_profile = normalize_generation_profile(generation_profile)
    active_diversity = normalize_diversity_profile(diversity_profile)
    text = combined_query_text(case)
    errors: List[str] = []
    fingerprint = query_fingerprint(case)

    if has_legacy_neo4j_index_syntax(text):
        errors.append("quality_gate:legacy_neo4j_index_syntax")
    if seen_fingerprints is not None and fingerprint in seen_fingerprints:
        errors.append("quality_gate:duplicate_query_fingerprint")
    if require_doc_grounding and not has_official_doc_grounding(source_record):
        errors.append("quality_gate:missing_official_doc_grounding")
    if NONDETERMINISTIC_RE.search(text):
        errors.append("quality_gate:nondeterministic_function")
    if active_profile != "neo4j_engine_stress" and GLOBAL_STATE_RE.search(text):
        errors.append("quality_gate:global_or_schema_state")
    if EXTENSION_RE.search(text):
        errors.append("quality_gate:extension_or_external_resource")
    if INTERNAL_ID_RE.search(text):
        errors.append("quality_gate:internal_identifier_dependency")
    if LIMIT_RE.search(text) and not ORDER_RE.search(text):
        errors.append("quality_gate:limit_without_order_by")
    if any(has_non_property_value_map_assignment(query) for query in case_query_texts(case)):
        errors.append("quality_gate:non_property_value_map_assignment")
    if any(has_call_subquery_returning_imported_names(query) for query in case_query_texts(case)):
        errors.append("quality_gate:call_subquery_returns_imported_variable_name")
    if active_profile in {"neo4j_semantic", "neo4j_cypher25", "neo4j_cypher25_metamorphic", "neo4j_internal_tier"} and DEPRECATED_CALL_IMPORT_RE.search(text):
        errors.append("quality_gate:deprecated_call_import_style")
    if active_profile in {"neo4j_semantic", "neo4j_cypher25", "neo4j_cypher25_metamorphic", "neo4j_internal_tier"} and CYPHER25_REMOVED_OR_DEPRECATED_RE.search(text):
        errors.append("quality_gate:cypher25_removed_or_deprecated_construct")
    if active_profile in {"neo4j_semantic", "neo4j_cypher25", "neo4j_cypher25_metamorphic", "neo4j_internal_tier"} and UNSUPPORTED_NULLABLE_TYPE_PREDICATE_RE.search(text):
        errors.append("quality_gate:unsupported_nullable_type_predicate_marker")
    if active_profile in {"neo4j_cypher25", "neo4j_cypher25_metamorphic"}:
        main_query = str(case.get("main_query") or "").strip()
        if main_query and not re.match(r"(?is)^CYPHER\s+25\b", main_query):
            errors.append("quality_gate:missing_cypher25_prefix")

    if active_profile == "portable_crossdb" and PORTABLE_FORBIDDEN_RE.search(text):
        errors.append("quality_gate:nonportable_neo4j_feature")

    tags = extract_quality_tags(case)
    if active_profile in {"neo4j_semantic", "neo4j_cypher25", "neo4j_cypher25_metamorphic"} and active_diversity in {"semantic_edges", "boundary_heavy"}:
        semantic_tags = {
            "call_scope",
            "conditional_query",
            "next_clause",
            "let_clause",
            "filter_clause",
            "all_quantifier",
            "for_clause",
            "type_predicate",
            "label_predicate",
            "property_exists",
            "vector",
            "path",
            "aggregation",
            "expression_subquery",
            "search",
            "collection_function",
            "string_function",
            "cypher25_function",
        }
        if not (set(tags) & semantic_tags):
            errors.append("quality_gate:missing_semantic_edge_feature")

    if active_profile == "neo4j_cypher25_metamorphic":
        main_query = str(case.get("main_query") or "")
        if re.search(r"\b(?:CREATE|MERGE|DELETE|DETACH\s+DELETE|SET|REMOVE|FOREACH|LOAD\s+CSV)\b", main_query, flags=re.I):
            errors.append("quality_gate:metamorphic_main_query_must_be_read_only")
        if WITH_NEXT_CONDITIONAL_SCOPE_RE.search(main_query):
            errors.append("quality_gate:use_return_before_next_conditional")
        if ELSE_WHEN_CONDITIONAL_RE.search(main_query):
            errors.append("quality_gate:invalid_else_when_conditional_query")
        if LET_SELF_REDECLARE_RE.search(main_query):
            errors.append("quality_gate:let_self_redeclares_existing_variable")
        if conditional_next_references_unreturned_dotted_variable(main_query):
            errors.append("quality_gate:conditional_next_references_unreturned_variable")
        metamorphic_tags = {
            "conditional_query",
            "next_clause",
            "let_clause",
            "filter_clause",
            "type_predicate",
            "call_scope",
            "all_quantifier",
            "expression_subquery",
        }
        if len(set(tags) & metamorphic_tags) < 2:
            errors.append("quality_gate:missing_metamorphic_cypher25_feature_combo")

    if active_profile == "neo4j_engine_stress":
        engine_stress_tags = {
            "schema_ddl",
            "relationship_constraint",
            "path_binding_write",
            "merge_on_create_match",
            "null_entity_side_effect",
            "extreme_literal",
            "multi_label_node",
            "temporal",
        }
        if len(set(tags) & engine_stress_tags) < 2:
            errors.append("quality_gate:missing_engine_stress_features")

    return errors


def attach_quality_gates(
    case: Dict[str, Any],
    source_record: Dict[str, Any],
    *,
    generation_profile: str,
    diversity_profile: str,
    require_doc_grounding: bool,
    seen_fingerprints: MutableSet[str] | None = None,
) -> Dict[str, Any]:
    active_profile = normalize_generation_profile(generation_profile)
    active_diversity = normalize_diversity_profile(diversity_profile)
    fingerprint = query_fingerprint(case)
    tags = extract_quality_tags(case)
    errors = quality_gate_errors(
        case,
        source_record,
        generation_profile=active_profile,
        diversity_profile=active_diversity,
        require_doc_grounding=require_doc_grounding,
        seen_fingerprints=seen_fingerprints,
    )
    validation = dict(case.get("validation") or {})
    validation_errors = list(validation.get("errors") or [])
    validation_errors.extend(errors)
    validation["errors"] = validation_errors
    validation["is_valid"] = bool(validation.get("is_valid")) and not errors
    case["validation"] = validation
    case["quality"] = {
        "generation_profile": active_profile,
        "diversity_profile": active_diversity,
        "require_doc_grounding": bool(require_doc_grounding),
        "has_official_doc_grounding": has_official_doc_grounding(source_record),
        "fingerprint": fingerprint,
        "tags": tags,
        "quality_gate_errors": errors,
    }
    return case


def summarize_quality(records: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    fingerprints = set()
    tag_counts: Counter[str] = Counter()
    error_counts: Counter[str] = Counter()
    grounded = 0
    total = 0
    for record in records:
        total += 1
        quality = record.get("quality") or {}
        fingerprint = str(quality.get("fingerprint") or "")
        if fingerprint:
            fingerprints.add(fingerprint)
        tag_counts.update(str(tag) for tag in quality.get("tags", []) or [])
        error_counts.update(str(err) for err in quality.get("quality_gate_errors", []) or [])
        if bool(quality.get("has_official_doc_grounding", False)):
            grounded += 1
    return {
        "records": total,
        "unique_fingerprints": len(fingerprints),
        "doc_grounded_records": grounded,
        "doc_grounding_rate": round(grounded / total, 4) if total else 0.0,
        "tag_counts": dict(sorted(tag_counts.items())),
        "quality_error_counts": dict(sorted(error_counts.items())),
    }
