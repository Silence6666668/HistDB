from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List

GROUP_ORDER = ["structure", "predicate", "aggregation", "ordering", "update", "feature", "other"]


def group_fragments(retrieval_results: List[Dict[str, Any]], per_group_k: int = 3) -> List[Dict[str, Any]]:
    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for item in retrieval_results:
        group = str(item.get("group_hint") or "other")
        if group not in GROUP_ORDER:
            group = "other"
        buckets[group].append(item)

    out: List[Dict[str, Any]] = []
    for group in GROUP_ORDER:
        items = buckets.get(group, [])
        if not items:
            continue
        items = sorted(items, key=lambda x: (-float(x.get("score", 0.0)), str(x.get("fragment_id", ""))))[:per_group_k]
        out.append({
            "group_type": group,
            "items": items,
        })
    return out
