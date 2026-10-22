from __future__ import annotations

import re
from typing import Any, Dict, List

from .common import clean_text, tokenize

CLAUSE_PATTERN = re.compile(
    r"\b(OPTIONAL\s+MATCH|MATCH|UNWIND|WITH|WHERE|CALL|CREATE|MERGE|SET|DELETE|REMOVE|RETURN|ORDER\s+BY|SKIP|LIMIT|UNION)\b",
    re.IGNORECASE,
)


def _feature_keywords(feature_records: List[Dict[str, Any]]) -> List[str]:
    keywords: List[str] = []
    for rec in feature_records:
        for key in ["feature_text", "text", "feature_name", "feature_description"]:
            value = rec.get(key)
            if value:
                keywords.append(clean_text(str(value)))
                break
        for tag in rec.get("tags", []) or []:
            tag = str(tag).strip().replace("_", " ")
            if tag:
                keywords.append(tag)
    out: List[str] = []
    seen: set[str] = set()
    for kw in keywords:
        low = kw.lower()
        if low and low not in seen:
            seen.add(low)
            out.append(kw)
    return out[:12]


def build_retrieval_query(skeleton_record: Dict[str, Any], feature_records: List[Dict[str, Any]]) -> Dict[str, Any]:
    skeleton_text = clean_text(str(skeleton_record.get("skeleton_text") or ""))
    clauses = []
    for m in CLAUSE_PATTERN.finditer(skeleton_text):
        clause = m.group(1).upper().replace("  ", " ")
        if not clauses or clauses[-1] != clause:
            clauses.append(clause)

    hole_needs = []
    for hole in skeleton_record.get("holes", []) or []:
        hole_type = str(hole.get("type") or hole.get("name") or "").strip()
        if hole_type:
            hole_needs.append(hole_type)

    feature_keywords = _feature_keywords(feature_records)
    structure_keywords = [c.lower().replace("_", " ") for c in clauses]
    structure_keywords.extend(str(x).replace("_", " ") for x in hole_needs)

    combined_keywords = []
    seen = set()
    for kw in structure_keywords + feature_keywords:
        low = kw.lower()
        if low and low not in seen:
            seen.add(low)
            combined_keywords.append(kw)

    retrieval_text = " ; ".join(combined_keywords)
    retrieval_tokens = tokenize(retrieval_text)

    return {
        "query_id": skeleton_record.get("query_id"),
        "skeleton_id": skeleton_record.get("skeleton_id"),
        "structure_keywords": structure_keywords,
        "feature_keywords": feature_keywords,
        "hole_needs": hole_needs,
        "retrieval_text": retrieval_text,
        "retrieval_tokens": retrieval_tokens,
    }
