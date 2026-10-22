from __future__ import annotations

import math
from typing import Any, Dict, List

from .common import build_idf, tokenize

SOURCE_BONUS = {
    "historical_query_clause": 0.25,
    "historical_query_full": 0.10,
    "feature": 0.18,
    "feature_evidence": 0.15,
    "skeleton": 0.12,
    "external": 0.08,
}


class LexicalRetriever:
    def __init__(self, fragments: List[Dict[str, Any]]) -> None:
        self.fragments = fragments
        self.fragment_tokens = [tokenize(f.get("text", "")) for f in fragments]
        self.idf = build_idf(self.fragment_tokens)

    def _score_overlap(self, query_tokens: List[str], fragment_tokens: List[str]) -> float:
        if not query_tokens or not fragment_tokens:
            return 0.0
        qset = set(query_tokens)
        fset = set(fragment_tokens)
        overlap = qset & fset
        if not overlap:
            return 0.0
        weighted = sum(self.idf.get(tok, 1.0) for tok in overlap)
        norm = math.sqrt(len(qset) * len(fset))
        return weighted / max(norm, 1.0)

    def retrieve(self, retrieval_query: Dict[str, Any], top_k: int = 8, exclude_query_id: str | None = None) -> List[Dict[str, Any]]:
        query_tokens = list(retrieval_query.get("retrieval_tokens", []))
        results: List[Dict[str, Any]] = []
        for frag, ftoks in zip(self.fragments, self.fragment_tokens):
            if exclude_query_id is not None and str(frag.get("query_id")) == str(exclude_query_id):
                continue
            base = self._score_overlap(query_tokens, ftoks)
            if base <= 0:
                continue
            source_type = str(frag.get("source_type") or "")
            group_hint = str(frag.get("group_hint") or "")
            bonus = SOURCE_BONUS.get(source_type, 0.0)
            if group_hint in {"structure", "predicate", "aggregation", "ordering", "update"}:
                bonus += 0.05
            score = base + bonus
            results.append({
                "fragment_id": frag.get("fragment_id"),
                "query_id": frag.get("query_id"),
                "source_id": frag.get("source_id"),
                "source_type": source_type,
                "group_hint": group_hint,
                "text": frag.get("text"),
                "tags": frag.get("tags", []),
                "metadata": frag.get("metadata", {}),
                "score": round(score, 6),
                "overlap_tokens": sorted(set(query_tokens) & set(ftoks)),
            })
        results.sort(key=lambda x: (-x["score"], x["fragment_id"]))
        return results[:top_k]
