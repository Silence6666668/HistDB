from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Dict, List

from src.common.io_utils import read_text
from src.common.text_utils import normalize_ws
from src.llm.model_client import BaseModelClient

CLAUSE_PATTERNS = [
    ("OPTIONAL MATCH", re.compile(r"\bOPTIONAL\s+MATCH\b", re.IGNORECASE)),
    ("MATCH", re.compile(r"\bMATCH\b", re.IGNORECASE)),
    ("UNWIND", re.compile(r"\bUNWIND\b", re.IGNORECASE)),
    ("WITH", re.compile(r"\bWITH\b", re.IGNORECASE)),
    ("WHERE", re.compile(r"\bWHERE\b", re.IGNORECASE)),
    ("CALL", re.compile(r"\bCALL\b", re.IGNORECASE)),
    ("CREATE", re.compile(r"\bCREATE\b", re.IGNORECASE)),
    ("MERGE", re.compile(r"\bMERGE\b", re.IGNORECASE)),
    ("SET", re.compile(r"\bSET\b", re.IGNORECASE)),
    ("DELETE", re.compile(r"\bDELETE\b", re.IGNORECASE)),
    ("REMOVE", re.compile(r"\bREMOVE\b", re.IGNORECASE)),
    ("RETURN", re.compile(r"\bRETURN\b", re.IGNORECASE)),
    ("ORDER BY", re.compile(r"\bORDER\s+BY\b", re.IGNORECASE)),
    ("SKIP", re.compile(r"\bSKIP\b", re.IGNORECASE)),
    ("LIMIT", re.compile(r"\bLIMIT\b", re.IGNORECASE)),
    ("UNION", re.compile(r"\bUNION\b", re.IGNORECASE)),
]

FUNCTION_RE = re.compile(
    r"\b(count|sum|avg|min|max|collect|size|labels|type|coalesce|exists|id|head|tail|nodes|relationships)\s*\(",
    re.IGNORECASE,
)
AGG_RE = re.compile(r"\b(count|sum|avg|min|max|collect)\s*\(", re.IGNORECASE)
PATTERN_COMP_RE = re.compile(r"\[[^\]]*\|[^\]]*\]", re.IGNORECASE)
SUBQUERY_RE = re.compile(r"\bCALL\s*\{", re.IGNORECASE)
PATH_RE = re.compile(r"\([^)]*\)-\[[^\]]*\]->\([^)]*\)|\([^)]*\)<-\[[^\]]*\]-\([^)]*\)", re.IGNORECASE)
WRITE_RE = re.compile(r"\b(CREATE|MERGE|SET|DELETE|REMOVE)\b", re.IGNORECASE)
OPTIONAL_RE = re.compile(r"\bOPTIONAL\s+MATCH\b", re.IGNORECASE)
ORDER_RE = re.compile(r"\bORDER\s+BY\b", re.IGNORECASE)
PAGINATION_RE = re.compile(r"\b(SKIP|LIMIT)\b", re.IGNORECASE)
CASE_RE = re.compile(r"\bCASE\b", re.IGNORECASE)
DISTINCT_RE = re.compile(r"\bDISTINCT\b", re.IGNORECASE)


@dataclass
class FeatureExtractionResult:
    feature_items: List[Dict[str, Any]]
    method: str
    notes: str = ""


def load_feature_prompt(path: str) -> str:
    return read_text(path)


def stable_feature_id(feature_name: str, feature_description: str) -> str:
    basis = f"{feature_name}||{feature_description}"
    h = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:12]
    return f"ft_{h}"


def extract_clause_sequence(query: str) -> List[str]:
    spans = []
    for name, pattern in CLAUSE_PATTERNS:
        for m in pattern.finditer(query):
            spans.append((m.start(), name))
    spans.sort(key=lambda x: x[0])
    ordered = []
    for _, name in spans:
        if not ordered or ordered[-1] != name:
            ordered.append(name)
    return ordered


def heuristic_extract_feature_items(query: str) -> FeatureExtractionResult:
    query_norm = normalize_ws(query)
    functions = sorted({m.group(1).lower() for m in FUNCTION_RE.finditer(query)})
    clause_sequence = extract_clause_sequence(query)

    flags = {
        "has_aggregation": bool(AGG_RE.search(query)),
        "has_optional_match": bool(OPTIONAL_RE.search(query)),
        "has_subquery": bool(SUBQUERY_RE.search(query)),
        "is_write": bool(WRITE_RE.search(query)),
        "has_path_pattern": bool(PATH_RE.search(query)),
        "has_order_by": bool(ORDER_RE.search(query)),
        "has_pagination": bool(PAGINATION_RE.search(query)),
        "has_pattern_comprehension": bool(PATTERN_COMP_RE.search(query)),
        "has_case": bool(CASE_RE.search(query)),
        "has_distinct": bool(DISTINCT_RE.search(query)),
    }

    tags = [c.lower().replace(" ", "_") for c in clause_sequence]
    if flags["has_aggregation"]:
        tags.append("aggregation")
    if flags["has_subquery"]:
        tags.append("subquery")
    if flags["is_write"]:
        tags.append("write")
    if flags["has_path_pattern"]:
        tags.append("path_pattern")
    if flags["has_pagination"]:
        tags.append("pagination")
    if flags["has_case"]:
        tags.append("case")
    if flags["has_distinct"]:
        tags.append("distinct")
    tags.extend(functions[:5])
    tags = sorted(dict.fromkeys(tags))

    signature = clause_sequence.copy()
    if flags["has_aggregation"]:
        signature.append("AGG")
    if flags["has_subquery"]:
        signature.append("SUBQUERY")
    if flags["is_write"]:
        signature.append("WRITE")
    if flags["has_pattern_comprehension"]:
        signature.append("PATTERN_COMP")

    desc_parts: List[str] = []
    if clause_sequence:
        desc_parts.append("该查询包含 " + "、".join(clause_sequence) + " 子句")
    if flags["has_aggregation"]:
        desc_parts.append("涉及聚合计算")
    if flags["has_optional_match"]:
        desc_parts.append("使用 OPTIONAL MATCH")
    if flags["has_subquery"]:
        desc_parts.append("包含子查询")
    if flags["has_order_by"]:
        desc_parts.append("带有排序逻辑")
    if flags["has_pagination"]:
        desc_parts.append("带有分页控制")
    if flags["is_write"]:
        desc_parts.append("属于写查询")
    if flags["has_path_pattern"]:
        desc_parts.append("包含图模式路径匹配")
    if not desc_parts:
        desc_parts.append("该查询具有基本的 Cypher 匹配与返回结构")

    feature_name = "query_pattern_" + ("_".join(tags[:4]) if tags else "basic")
    feature_description = "，".join(desc_parts) + "。"

    item = {
        "feature_name": feature_name[:80],
        "feature_description": feature_description,
        "query_instance": query_norm,
        "tags": tags[:12],
        "feature_signature": signature,
        "flags": flags,
        "entities": {
            "functions": functions,
            "clauses": clause_sequence,
        },
    }
    return FeatureExtractionResult(feature_items=[item], method="heuristic")


def llm_extract_feature_items(query_text: str, client: BaseModelClient, system_prompt: str) -> FeatureExtractionResult:
    user_prompt = (
        "Please extract reusable testing-relevant feature items from the following concrete Cypher query. Return JSON only.\n\n"
        f"QUERY:\n{query_text}\n"
    )
    data = client.chat_json(system_prompt=system_prompt, user_prompt=user_prompt)
    items = data.get("feature_items", [])
    notes = str(data.get("notes", ""))
    if not isinstance(items, list) or not items:
        raise ValueError("LLM returned empty feature_items")

    normalized_items: List[Dict[str, Any]] = []
    query_norm = normalize_ws(query_text)
    for item in items:
        if not isinstance(item, dict):
            continue
        feature_name = normalize_ws(str(item.get("feature_name", ""))).strip()
        feature_description = normalize_ws(str(item.get("feature_description", ""))).strip()
        query_instance = normalize_ws(str(item.get("query_instance", ""))).strip() or query_norm
        tags = item.get("tags", [])
        signature = item.get("feature_signature", [])
        if not isinstance(tags, list):
            tags = []
        if not isinstance(signature, list):
            signature = []
        tags = [normalize_ws(str(t)).strip() for t in tags if str(t).strip()]
        signature = [normalize_ws(str(s)).strip() for s in signature if str(s).strip()]
        if not feature_name or not feature_description:
            continue
        normalized_items.append({
            "feature_name": feature_name,
            "feature_description": feature_description,
            "query_instance": query_instance,
            "tags": tags[:12],
            "feature_signature": signature[:12],
        })

    if not normalized_items:
        raise ValueError("No valid feature items after normalization")
    return FeatureExtractionResult(feature_items=normalized_items, method="llm", notes=notes)
