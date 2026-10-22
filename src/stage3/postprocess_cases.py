from __future__ import annotations

import re
from typing import Any, Dict, List

from src.common.text_utils import normalize_ws

_CODE_FENCE_RE = re.compile(r'^```[a-zA-Z0-9_+-]*\s*|```$', re.MULTILINE)
_REL_UNIQUE_CONSTRAINT_RE = re.compile(
    r"^\s*CREATE\s+CONSTRAINT"
    r"(?:\s+(?P<name>`?[A-Za-z_][A-Za-z0-9_]*`?))?"
    r"(?P<if_not_exists>\s+IF\s+NOT\s+EXISTS)?"
    r"\s+FOR\s*\(\)\s*-\s*\[(?P<var>[A-Za-z_][A-Za-z0-9_]*)?\s*:\s*(?P<rel>`?[A-Za-z_][A-Za-z0-9_]*`?)\]\s*-\s*\(\)"
    r"\s+REQUIRE\s*\(?(?:(?P=var)\s*\.)?(?P<prop>`?[A-Za-z_][A-Za-z0-9_]*`?)\)?\s+IS\s+UNIQUE\s*$",
    re.IGNORECASE,
)
_DROP_REL_UNIQUE_CONSTRAINT_BY_PATTERN_RE = re.compile(
    r"^\s*DROP\s+CONSTRAINT(?:\s+IF\s+EXISTS)?\s+FOR\s*\(\)\s*-\s*\[(?P<var>[A-Za-z_][A-Za-z0-9_]*)?\s*:\s*(?P<rel>`?[A-Za-z_][A-Za-z0-9_]*`?)\]\s*-\s*\(\)"
    r"\s+REQUIRE\s*\(?(?:(?P=var)\s*\.)?(?P<prop>`?[A-Za-z_][A-Za-z0-9_]*`?)\)?\s+IS\s+UNIQUE\s*$",
    re.IGNORECASE,
)


def strip_code_fence(text: str) -> str:
    return normalize_ws(_CODE_FENCE_RE.sub('', text or ''))


def normalize_query_list(values: Any) -> List[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    out = []
    if isinstance(values, list):
        for v in values:
            if not isinstance(v, str):
                continue
            q = strip_code_fence(v)
            if q:
                out.append(q)
    return out


def _safe_identifier(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", value.strip("`")).strip("_") or "rel"


def _relationship_unique_constraint_name(rel_type: str, prop: str) -> str:
    return f"engine_stress_rel_unique_{_safe_identifier(rel_type)}_{_safe_identifier(prop)}"


def _normalize_engine_stress_schema_cleanup(result: Dict[str, Any]) -> None:
    """Normalize LLM-produced relationship uniqueness DDL to named constraints.

    Neo4j cleanup should use `DROP CONSTRAINT name IF EXISTS`; pattern-form
    drops are not portable across versions and create avoidable cleanup noise.
    """
    constraint_names: dict[tuple[str, str], str] = {}
    normalized_setup: List[str] = []
    for query in result.get("setup_queries", []) or []:
        match = _REL_UNIQUE_CONSTRAINT_RE.match(query)
        if not match:
            normalized_setup.append(query)
            continue
        rel_type = match.group("rel")
        prop = match.group("prop")
        name = match.group("name") or _relationship_unique_constraint_name(rel_type, prop)
        constraint_names[(rel_type.strip("`").lower(), prop.strip("`").lower())] = name.strip("`")
        var = match.group("var") or "r"
        normalized_setup.append(
            f"CREATE CONSTRAINT {name.strip('`')} IF NOT EXISTS "
            f"FOR ()-[{var}:{rel_type}]-() REQUIRE ({var}.{prop}) IS UNIQUE"
        )
    result["setup_queries"] = normalized_setup

    normalized_cleanup: List[str] = []
    for query in result.get("cleanup_queries", []) or []:
        match = _DROP_REL_UNIQUE_CONSTRAINT_BY_PATTERN_RE.match(query)
        if match:
            key = (match.group("rel").strip("`").lower(), match.group("prop").strip("`").lower())
            name = constraint_names.get(key) or _relationship_unique_constraint_name(match.group("rel"), match.group("prop"))
            normalized_cleanup.append(f"DROP CONSTRAINT {name.strip('`')} IF EXISTS")
            continue
        normalized_cleanup.append(query)

    existing_drop_names = {
        m.group(1).strip("`").lower()
        for q in normalized_cleanup
        if (m := re.match(r"^\s*DROP\s+CONSTRAINT\s+(`?[A-Za-z_][A-Za-z0-9_]*`?)\b", q, re.IGNORECASE))
    }
    for name in constraint_names.values():
        if name.lower() not in existing_drop_names:
            normalized_cleanup.insert(0, f"DROP CONSTRAINT {name} IF EXISTS")
    result["cleanup_queries"] = normalized_cleanup


def postprocess_case(case: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(case)
    result['setup_queries'] = normalize_query_list(case.get('setup_queries'))
    result['cleanup_queries'] = normalize_query_list(case.get('cleanup_queries'))
    result['main_query'] = strip_code_fence(str(case.get('main_query') or ''))
    result['notes'] = normalize_ws(str(case.get('notes') or ''))
    if str(result.get("generation_profile") or "").strip().lower() == "neo4j_engine_stress":
        _normalize_engine_stress_schema_cleanup(result)
    return result
