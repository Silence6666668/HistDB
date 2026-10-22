"""Build paired, generation-free RQ2 top-up inputs with recoverable history.

This module never calls an LLM or a DBMS. Existing accepted/rejected outcomes
have no role in candidate ordering or feature sampling. Restore all committed
inputs (not only accepted outputs) when constructing a resumed builder.
"""
from __future__ import annotations

import copy
import random
import re
from collections import OrderedDict, defaultdict

from scripts.build_random_conditioned_skeleton_ablation import (
    _feature_only_row,
    _require_skeletons,
    build_feature_only_retrieval_query,
    deduplicate_features,
)
from scripts.build_within_report_skeleton_ablation import group_features_by_source
from src.stage2.build_fragment_corpus import build_fragment_corpus
from src.stage2.feature_selection import (
    score_skeleton_feature_affinity,
    select_feature_records,
)
from src.stage2.group_fragments import group_fragments
from src.stage2.retriever import LexicalRetriever


class InputBuilder:
    """Sequential deterministic batches; C/S and R reserve sets per skeleton.

    ``start_index`` is the next *new* candidate number. ``prior_inputs`` accepts
    arm letters or the existing long lane names, and seeds historical unordered
    feature sets. W deliberately permits repeated same-report combinations.
    Replaying the same input prefix restores the same subsequent selections.
    """

    def __init__(
        self,
        base_inputs: list[dict],
        skeletons: list[dict],
        features: list[dict],
        extra_fragments: list[dict],
        *,
        seed: int = 20261013,
        top_k: int = 8,
        per_group_k: int = 3,
        prior_inputs: dict[str, list[dict]] | None = None,
        start_index: int = 0,
    ) -> None:
        if not isinstance(start_index, int) or start_index < 0:
            raise ValueError("start_index must be a nonnegative integer")
        if top_k <= 0 or per_group_k <= 0:
            raise ValueError("top_k and per_group_k must be positive")
        self._seed = seed
        self._top_k = top_k
        self._per_group_k = per_group_k
        self._next_index = start_index
        self._skeletons = _require_skeletons(base_inputs, skeletons)
        self._features = deduplicate_features(features)
        if len(self._features) < 3:
            raise ValueError("feature pool needs at least 3 distinct feature IDs")
        self._features_by_source = group_features_by_source(features)

        # Deduplicate the original task IDs before outcome-independent shuffling.
        self._eligible: list[dict] = []
        seen: set[str] = set()
        sources: set[str] = set()
        for base in base_inputs:
            original_id = str(base["query_id"]).strip()
            if original_id in seen:
                continue
            seen.add(original_id)
            skeleton = self._skeletons[original_id]
            source_id = str(skeleton.get("source_id") or "").strip()
            same_report = self._features_by_source.get(source_id, [])
            positive = sum(score_skeleton_feature_affinity(skeleton, feature) > 0 for feature in same_report)
            if positive >= 3:
                self._eligible.append(base)
                sources.add(source_id)
        self.eligible_count = len(self._eligible)
        self.source_report_count = len(sources)
        if not self._eligible:
            raise ValueError("no eligible tasks with 3 same-report positive-affinity features")
        random.Random(seed).shuffle(self._eligible)

        self._used: dict[str, dict[str, set[frozenset[str]]]] = {
            "R": defaultdict(set), "C": defaultdict(set),
        }
        self._draws: dict[str, dict[str, int]] = {
            "R": defaultdict(int), "C": defaultdict(int),
        }
        self._restore_history(prior_inputs or {})

        # Rebuild from the current raw corpus; never reuse base fragment_groups.
        # build_fragment_corpus applies the current official-example decisions.
        self._retriever = LexicalRetriever(build_fragment_corpus(
            skeletons, self._features, extra_fragments=extra_fragments,
        ))
        # Cache only token membership. Every surviving score, bonus, tie-break,
        # exclusion, and grouping still runs through the unchanged implementation.
        self._token_positions: dict[str, set[int]] = defaultdict(set)
        for index, tokens in enumerate(self._retriever.fragment_tokens):
            for token in set(tokens):
                self._token_positions[token].add(index)
        self._group_cache: OrderedDict[tuple, list[dict]] = OrderedDict()

    @staticmethod
    def _skeleton_key(skeleton: dict, original_id: str) -> str:
        return str(skeleton.get("skeleton_id") or original_id).strip()

    def _historical_skeleton_key(self, row: dict) -> str:
        # New rows retain skeleton_id even when it is not model guidance.
        skeleton_id = row.get("skeleton_id") or row.get("feature_selection_skeleton_key") or row.get("selected_skeleton_id")
        if skeleton_id:
            return str(skeleton_id).strip()
        for field in ("original_query_id", "pilot_original_query_id", "seed_query_id", "query_id"):
            original_id = str(row.get(field) or "").strip()
            candidates = (original_id, original_id.split(":", 1)[-1])
            for candidate in candidates:
                skeleton = self._skeletons.get(candidate)
                if skeleton is not None:
                    return self._skeleton_key(skeleton, candidate)
        raise ValueError(f"cannot recover original skeleton for prior input {row.get('query_id')}")

    def _restore_history(self, prior_inputs: dict[str, list[dict]]) -> None:
        aliases = {
            "R": "R", "random_feature_only": "R",
            "C": "C", "conditioned_feature_only": "C",
            "S": "C", "skeleton_feature": "C",
            "W": None, "within_report_skeleton": None,
        }
        seen: set[tuple] = set()
        for name, rows in prior_inputs.items():
            if name not in aliases:
                raise ValueError(f"unknown prior input arm: {name}")
            arm = aliases[name]
            if arm is None:
                continue
            for row in rows:
                task_id = str(row.get("query_id") or "").strip()
                combination = frozenset(str(value).strip() for value in row.get("selected_feature_ids", []))
                if not task_id or len(combination) != 3 or "" in combination:
                    raise ValueError(f"prior input requires a task ID and 3 distinct feature IDs: {name}/{task_id}")
                key = self._historical_skeleton_key(row)
                identity = (arm, key, self._historical_selection_event(row, task_id), combination)
                if identity in seen:
                    continue
                seen.add(identity)
                self._used[arm][key].add(combination)
                self._draws[arm][key] += 1

    @staticmethod
    def _historical_selection_event(row: dict, task_id: str) -> tuple:
        """Identify a source selection across C/S paths and copied pilot rows.

        Formal input IDs repeat across generation seeds. A pilot may copy that
        same seed/task/selection under a prefixed ID. Different selections from
        the same historical seed/task remain distinct via the caller's feature
        set identity; identical C/S and pilot copies count only once.
        """
        provenance = row.get("rq2_provenance") or {}
        seed = row.get("pilot_generation_seed", provenance.get("generation_seed"))
        if seed is None:
            matches = re.findall(
                r"(?:^|[\\/])seed_(\d+)(?=[\\/]|$)",
                str(provenance.get("input_path") or ""),
            )
            if matches:
                seed = matches[-1]
        prefixed = re.match(r"^seed_(\d+):(.*)$", task_id)
        if seed is None and prefixed:
            seed = prefixed.group(1)
        if seed is None:
            # Current input query IDs already identify distinct candidate draws.
            return (None, task_id)
        original_id = str(
            row.get("pilot_original_query_id") or row.get("original_query_id")
            or (prefixed.group(2) if prefixed else task_id)
        ).strip()
        return (int(seed), original_id)

    def _retrieve_groups(self, retrieval_query: dict, original_id: str) -> list[dict]:
        token_sequence = tuple(retrieval_query.get("retrieval_tokens", []))
        tokens = frozenset(token_sequence)
        cache_key = (token_sequence, original_id)
        cached = self._group_cache.get(cache_key)
        if cached is not None:
            self._group_cache.move_to_end(cache_key)
            return cached
        positions: set[int] = set()
        for token in tokens:
            positions.update(self._token_positions.get(token, ()))
        ordered = sorted(positions)
        # A view shares full-corpus IDF and original tokens/order. Fragments
        # without token overlap would have score zero and be discarded anyway.
        view = object.__new__(LexicalRetriever)
        view.fragments = [self._retriever.fragments[index] for index in ordered]
        view.fragment_tokens = [self._retriever.fragment_tokens[index] for index in ordered]
        view.idf = self._retriever.idf
        groups = group_fragments(view.retrieve(
            retrieval_query, top_k=self._top_k, exclude_query_id=original_id,
        ), per_group_k=self._per_group_k)
        self._group_cache[cache_key] = groups
        if len(self._group_cache) > 256:
            self._group_cache.popitem(last=False)
        return groups

    def _row(self, base: dict, selected: list[dict], *, selection: str, draw_index: int) -> dict:
        original_id = base["original_query_id"]
        query = build_feature_only_retrieval_query(base["query_id"], selected)
        row = _feature_only_row(
            base, selected, query, self._retrieve_groups(query, original_id),
            feature_selection=selection, seed=self._seed,
        )
        row.update(draw_index=draw_index, feature_selection_draw_index=draw_index)
        return row

    def make_batch(self, start: int, count: int) -> dict[str, list[dict]]:
        """Return new W/R/C/S inputs; only a complete batch advances state."""
        if not isinstance(start, int) or start != self._next_index:
            raise ValueError(f"start must equal next contiguous candidate index {self._next_index}")
        if not isinstance(count, int) or count <= 0:
            raise ValueError("count must be a positive integer")
        # Keep failed/exhausted batches retryable without partially reserving IDs.
        used = {arm: defaultdict(set, {key: set(value) for key, value in histories.items()}) for arm, histories in self._used.items()}
        draws = {arm: defaultdict(int, histories) for arm, histories in self._draws.items()}
        result: dict[str, list[dict]] = {arm: [] for arm in ("W", "R", "C", "S")}
        for index in range(start, start + count):
            source_base = self._eligible[index % self.eligible_count]
            original_id = str(source_base["query_id"]).strip()
            skeleton = self._skeletons[original_id]
            key = self._skeleton_key(skeleton, original_id)
            c_draw, r_draw = draws["C"][key], draws["R"][key]
            base = copy.deepcopy(source_base)
            base.update(
                query_id=f"rq2_current_{index:07d}:{original_id}",
                original_query_id=original_id,
                skeleton_id=skeleton.get("skeleton_id"),
                rq2_candidate_index=index,
                feature_selection_skeleton_key=key,
                feature_combination_deduplication="unordered_feature_ids_per_original_skeleton",
            )
            random_features = select_feature_records(
                {"query_id": original_id, "skeleton_text": "", "holes": []},
                query_features=[], all_features=self._features, mode="random", sample_size=3,
                seed=self._seed, draw_index=r_draw, used_combinations=used["R"][key],
            )
            conditioned_features = select_feature_records(
                skeleton, query_features=[], all_features=self._features,
                mode="random_constrained", sample_size=3, seed=self._seed,
                draw_index=c_draw, used_combinations=used["C"][key],
            )
            source_id = str(skeleton.get("source_id") or "").strip()
            within_features = select_feature_records(
                skeleton, query_features=[], all_features=self._features_by_source[source_id],
                mode="random_constrained", sample_size=3, seed=self._seed,
                rng_salt="within-report", draw_index=c_draw,
            )
            r = self._row(base, random_features, selection="random", draw_index=r_draw)
            c = self._row(base, conditioned_features, selection="random_constrained", draw_index=c_draw)
            s = copy.deepcopy(c)
            s.update(
                selected_skeleton_id=skeleton.get("skeleton_id"),
                guidance_skeleton_text=str(skeleton.get("skeleton_text") or ""),
                generation_strategy="feature-driven-soft",
            )
            w = self._row(base, within_features, selection="within_report_random_constrained", draw_index=c_draw)
            w.update(
                selected_skeleton_id=skeleton.get("skeleton_id"),
                guidance_skeleton_text=str(skeleton.get("skeleton_text") or ""),
                generation_strategy="feature-driven-soft",
                composition_scope="within_report_features",
                same_report_source_id=source_id,
                selected_feature_source_ids=sorted({str(feature.get("source_id") or "").strip() for feature in within_features}),
                feature_combination_deduplication="not_applicable_within_report",
            )
            for arm, row in (("W", w), ("R", r), ("C", c), ("S", s)):
                result[arm].append(row)
            draws["R"][key] += 1
            draws["C"][key] += 1
        self._used, self._draws = used, draws
        self._next_index = start + count
        return result
