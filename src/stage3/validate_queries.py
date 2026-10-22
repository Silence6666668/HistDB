from __future__ import annotations

import re
from typing import Any, Dict, List

HOLE_RE = re.compile(r"\bHOLE_[A-Z0-9_]+\b")
CYTHER_ENTRY_RE = re.compile(
    r"^\s*(?:"
    r"match|optional\s+match|create|merge|call|return|with|unwind|"
    r"drop|show|load\s+csv|use|profile|explain|cypher|foreach"
    r")\b",
    re.IGNORECASE,
)


def _has_unresolved_hole(text: str) -> bool:
    return bool(HOLE_RE.search(text or ""))


def validate_case(case: Dict[str, Any]) -> Dict[str, Any]:
    errors: List[str] = []
    if not case.get('main_query'):
        errors.append('empty_main_query')
    main_query = str(case.get('main_query') or '')
    if _has_unresolved_hole(main_query):
        errors.append('main_query_has_unresolved_holes')
    if not CYTHER_ENTRY_RE.search(main_query):
        errors.append('main_query_not_cypher_like')
    for query in case.get('setup_queries', []) or []:
        if _has_unresolved_hole(str(query)):
            errors.append('setup_query_has_unresolved_holes')
            break
    for query in case.get('cleanup_queries', []) or []:
        if _has_unresolved_hole(str(query)):
            errors.append('cleanup_query_has_unresolved_holes')
            break
    return {
        'is_valid': not errors,
        'errors': errors,
    }
