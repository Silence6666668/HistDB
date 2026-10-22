from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, List


def build_skeleton_pool(records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for rec in records:
        groups[rec["skeleton_text"]].append(rec)

    pool: List[Dict[str, Any]] = []
    for skeleton_text, items in groups.items():
        pool.append(
            {
                "skeleton_id": items[0]["skeleton_id"],
                "skeleton_text": skeleton_text,
                "count": len(items),
                "sample_query_ids": [x["query_id"] for x in items[:10]],
                "methods": sorted({x.get("method", "") for x in items}),
            }
        )
    pool.sort(key=lambda x: (-x["count"], x["skeleton_id"]))
    return pool


def build_feature_pool(records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for rec in records:
        feature_name = str(rec.get("feature_name") or "").strip()
        feature_description = str(rec.get("feature_description") or "").strip()
        key = feature_name or feature_description
        groups[key].append(rec)

    pool: List[Dict[str, Any]] = []
    for key, items in groups.items():
        sample = items[0]
        pool.append(
            {
                "feature_group_id": sample.get("feature_id") or f"fg_{abs(hash(key)) % 10**10:010d}",
                "feature_name": sample.get("feature_name"),
                "feature_description": sample.get("feature_description"),
                "count": len(items),
                "sample_query_ids": [x["query_id"] for x in items[:10]],
                "sample_query_instances": [x.get("query_instance", "") for x in items[:3]],
                "tags": _merge_list_field(items, "tags", limit=20),
                "feature_signatures": _merge_signature_variants(items),
                "methods": sorted({x.get("method", "") for x in items}),
            }
        )
    pool.sort(key=lambda x: (-x["count"], str(x["feature_name"] or x["feature_group_id"])))
    return pool


def _merge_list_field(items: List[Dict[str, Any]], field: str, limit: int = 20) -> List[str]:
    merged: List[str] = []
    seen = set()
    for item in items:
        for v in item.get(field, []) or []:
            s = str(v).strip()
            if s and s not in seen:
                seen.add(s)
                merged.append(s)
                if len(merged) >= limit:
                    return merged
    return merged


def _merge_signature_variants(items: List[Dict[str, Any]], limit: int = 5) -> List[List[str]]:
    merged: List[List[str]] = []
    seen = set()
    for item in items:
        sig = tuple(str(x).strip() for x in (item.get("feature_signature") or []) if str(x).strip())
        if sig and sig not in seen:
            seen.add(sig)
            merged.append(list(sig))
            if len(merged) >= limit:
                return merged
    return merged
