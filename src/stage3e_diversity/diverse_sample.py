from __future__ import annotations

import hashlib
import json
import random
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List

from src.common.jsonl_utils import load_jsonl, write_jsonl


CLAUSE_PATTERNS: list[tuple[str, str]] = [
    ("CALL", r"\bCALL\s*\{|\bCALL\s*\("),
    ("UNION", r"\bUNION(?:\s+ALL)?\b"),
    ("OPTIONAL MATCH", r"\bOPTIONAL\s+MATCH\b"),
    ("MATCH", r"\bMATCH\b"),
    ("WITH", r"\bWITH\b"),
    ("UNWIND", r"\bUNWIND\b"),
    ("RETURN", r"\bRETURN\b"),
    ("ORDER BY", r"\bORDER\s+BY\b"),
    ("LIMIT", r"\bLIMIT\b"),
    ("CREATE", r"\bCREATE\b"),
    ("MERGE", r"\bMERGE\b"),
    ("SET", r"\bSET\b"),
    ("DELETE", r"\bDETACH\s+DELETE\b|\bDELETE\b"),
    ("REMOVE", r"\bREMOVE\b"),
]

AGG_PATTERNS: list[tuple[str, str]] = [
    ("count", r"\bCOUNT\s*\("),
    ("collect", r"\bCOLLECT\s*\("),
    ("sum", r"\bSUM\s*\("),
    ("avg", r"\bAVG\s*\("),
    ("min", r"\bMIN\s*\("),
    ("max", r"\bMAX\s*\("),
]


@dataclass
class AnalyzedCase:
    raw: Dict[str, Any]
    bucket: str
    signature: str
    signature_hash: str
    analysis: Dict[str, Any]


def _count_matches(text: str, pattern: str) -> int:
    return len(re.findall(pattern, text, flags=re.IGNORECASE))


def _has(text: str, pattern: str) -> bool:
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def _extract_clause_sequence(query: str) -> list[str]:
    found: list[tuple[int, str]] = []
    for name, pattern in CLAUSE_PATTERNS:
        for m in re.finditer(pattern, query, flags=re.IGNORECASE):
            found.append((m.start(), name))
    found.sort(key=lambda item: item[0])
    sequence: list[str] = []
    for _, name in found:
        if not sequence or sequence[-1] != name:
            sequence.append(name)
    return sequence


def _extract_aggregations(query: str) -> list[str]:
    hits: list[str] = []
    for name, pattern in AGG_PATTERNS:
        if _has(query, pattern):
            hits.append(name)
    return hits


def _classify_bucket(query: str, clause_sequence: list[str], aggs: list[str]) -> str:
    has_call = "CALL" in clause_sequence
    has_union = "UNION" in clause_sequence
    has_optional = "OPTIONAL MATCH" in clause_sequence
    has_order = "ORDER BY" in clause_sequence
    has_with = "WITH" in clause_sequence
    has_distinct = _has(query, r"\bWITH\s+DISTINCT\b|\bRETURN\s+DISTINCT\b")
    has_path = _has(query, r"\bpath\s*=") or _has(query, r"\*\d+\.\.\d+") or _has(query, r"\ballShortestPaths\b|\bshortestPath\b")
    has_projection = _has(query, r"\{\s*\.[^}]+\}") or _has(query, r"RETURN\s+\{") or _has(query, r"AS\s+result\b")
    has_write = any(name in clause_sequence for name in ("CREATE", "MERGE", "SET", "DELETE", "REMOVE"))
    has_read_before_write = has_write and any(name in clause_sequence for name in ("MATCH", "OPTIONAL MATCH", "WITH"))

    if has_call and has_union:
        return "call_union_scope"
    if has_optional and aggs:
        return "optional_agg"
    if has_path:
        return "path_varlen"
    if has_projection or has_order:
        return "projection_order"
    if has_read_before_write:
        return "write_after_read"
    if has_with and has_distinct:
        return "distinct_scope"
    if clause_sequence and set(clause_sequence).issubset({"MATCH", "RETURN", "ORDER BY", "LIMIT", "WITH"}):
        return "match_only"
    return "other"


def _build_signature(query: str, clause_sequence: list[str], aggs: list[str], bucket: str) -> str:
    features = {
        "bucket": bucket,
        "clauses": clause_sequence,
        "aggregations": aggs,
        "has_call_paren": _has(query, r"\bCALL\s*\("),
        "has_call_subquery": _has(query, r"\bCALL\s*\{"),
        "has_union_all": _has(query, r"\bUNION\s+ALL\b"),
        "has_optional": "OPTIONAL MATCH" in clause_sequence,
        "has_path": _has(query, r"\bpath\s*=") or _has(query, r"\*\d+\.\.\d+"),
        "has_projection": _has(query, r"\{\s*\.[^}]+\}") or _has(query, r"RETURN\s+\{"),
        "has_distinct": _has(query, r"\bDISTINCT\b"),
        "has_case": _has(query, r"\bCASE\b"),
        "has_coalesce": _has(query, r"\bCOALESCE\s*\("),
        "has_unwind": "UNWIND" in clause_sequence,
        "has_order": "ORDER BY" in clause_sequence,
        "has_limit": "LIMIT" in clause_sequence,
        "has_write": any(name in clause_sequence for name in ("CREATE", "MERGE", "SET", "DELETE", "REMOVE")),
    }
    return json.dumps(features, ensure_ascii=False, sort_keys=True)


def analyze_case(case: Dict[str, Any]) -> AnalyzedCase:
    query = str(case.get("main_query", "") or "")
    clause_sequence = _extract_clause_sequence(query)
    aggs = _extract_aggregations(query)
    bucket = _classify_bucket(query, clause_sequence, aggs)
    signature = _build_signature(query, clause_sequence, aggs, bucket)
    signature_hash = hashlib.sha1(signature.encode("utf-8")).hexdigest()[:12]
    analysis = {
        "query_id": case.get("query_id"),
        "case_id": case.get("case_id"),
        "bucket": bucket,
        "clause_sequence": clause_sequence,
        "aggregations": aggs,
        "signature": json.loads(signature),
        "signature_hash": signature_hash,
    }
    return AnalyzedCase(
        raw=case,
        bucket=bucket,
        signature=signature,
        signature_hash=signature_hash,
        analysis=analysis,
    )


def _bucket_quota_map(quota: int) -> Dict[str, int]:
    return {
        "call_union_scope": quota,
        "optional_agg": quota,
        "path_varlen": quota,
        "projection_order": quota,
        "write_after_read": quota,
        "distinct_scope": quota,
        "match_only": quota,
        "other": quota,
    }


def build_diverse_sample(
    input_path: str | Path,
    output_dir: str | Path,
    *,
    bucket_quota: int = 40,
    per_signature_cap: int = 2,
    max_items: int | None = None,
    seed: int = 123,
) -> Dict[str, Any]:
    records = load_jsonl(input_path)
    if max_items is not None:
        records = records[:max_items]

    analyzed = [analyze_case(rec) for rec in records]
    analysis_records = [a.analysis for a in analyzed]

    bucket_counts = Counter(a.bucket for a in analyzed)
    signature_counts = Counter((a.bucket, a.signature_hash) for a in analyzed)

    quota_map = _bucket_quota_map(bucket_quota)
    rng = random.Random(seed)

    by_bucket: Dict[str, List[AnalyzedCase]] = defaultdict(list)
    for a in analyzed:
        by_bucket[a.bucket].append(a)

    for items in by_bucket.values():
        rng.shuffle(items)

    selected: List[AnalyzedCase] = []
    selected_per_bucket: Counter[str] = Counter()
    selected_per_signature: Counter[tuple[str, str]] = Counter()

    bucket_order = [
        "optional_agg",
        "path_varlen",
        "projection_order",
        "write_after_read",
        "distinct_scope",
        "call_union_scope",
        "match_only",
        "other",
    ]

    made_progress = True
    while made_progress:
        made_progress = False
        for bucket in bucket_order:
            if selected_per_bucket[bucket] >= quota_map.get(bucket, bucket_quota):
                continue
            items = by_bucket.get(bucket, [])
            picked = None
            for idx, item in enumerate(items):
                sig_key = (bucket, item.signature_hash)
                if selected_per_signature[sig_key] >= per_signature_cap:
                    continue
                picked = items.pop(idx)
                break
            if picked is None:
                continue
            selected.append(picked)
            selected_per_bucket[bucket] += 1
            selected_per_signature[(bucket, picked.signature_hash)] += 1
            made_progress = True

    selected_records: List[Dict[str, Any]] = []
    for item in selected:
        rec = dict(item.raw)
        rec["diversity_bucket"] = item.bucket
        rec["diversity_signature_hash"] = item.signature_hash
        rec["diversity_clause_sequence"] = item.analysis["clause_sequence"]
        rec["diversity_aggregations"] = item.analysis["aggregations"]
        selected_records.append(rec)

    out_dir = Path(output_dir)
    write_jsonl(analysis_records, out_dir / "analysis.jsonl")
    write_jsonl(selected_records, out_dir / "diverse_sample.jsonl")

    summary = {
        "input_path": str(input_path),
        "output_dir": str(output_dir),
        "input_cases": len(records),
        "analyzed_cases": len(analyzed),
        "selected_cases": len(selected_records),
        "bucket_quota": bucket_quota,
        "per_signature_cap": per_signature_cap,
        "seed": seed,
        "bucket_counts": dict(bucket_counts),
        "selected_bucket_counts": dict(selected_per_bucket),
        "unique_signatures": len(signature_counts),
        "selected_unique_signatures": len({(x.bucket, x.signature_hash) for x in selected}),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary
