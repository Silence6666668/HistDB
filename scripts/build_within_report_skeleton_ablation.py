from __future__ import annotations

import argparse
import copy
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.build_random_conditioned_skeleton_ablation import (
    _coerce_builder_inputs,
    _feature_only_row,
    _require_skeletons,
    _sha256,
    build_feature_only_retrieval_query,
    deduplicate_features,
)
from src.common.jsonl_utils import iter_jsonl, write_jsonl
from src.stage2.build_fragment_corpus import build_fragment_corpus
from src.stage2.feature_selection import (
    score_skeleton_feature_affinity,
    select_feature_records,
)
from src.stage2.group_fragments import group_fragments
from src.stage2.retriever import LexicalRetriever


EXPECTED_INPUT_TASKS = 1000


def group_features_by_source(
    records: Iterable[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Deduplicate feature IDs within each report, never across reports."""
    grouped: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in records:
        source_id = str(row.get("source_id") or "").strip()
        feature_id = str(row.get("feature_id") or "").strip()
        if source_id and feature_id and feature_id not in grouped[source_id]:
            grouped[source_id][feature_id] = row
    return {
        source_id: [by_id[key] for key in sorted(by_id)]
        for source_id, by_id in sorted(grouped.items())
    }


def build_within_report_records(
    base_inputs: Iterable[dict[str, Any]] | Mapping[str, Any],
    skeletons: Iterable[dict[str, Any]] | None = None,
    features: Iterable[dict[str, Any]] | None = None,
    extra_fragments: Iterable[dict[str, Any]] | None = None,
    *,
    seed: int = 20260921,
    feature_count: int = 3,
    top_k: int = 8,
    per_group_k: int = 3,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    (
        base_rows,
        skeleton_rows,
        feature_rows,
        extra_fragment_rows,
        seed,
        feature_count,
        top_k,
        per_group_k,
    ) = _coerce_builder_inputs(
        base_inputs,
        skeletons,
        features,
        extra_fragments,
        seed,
        feature_count,
        top_k,
        per_group_k,
    )
    skeleton_by_query_id = _require_skeletons(base_rows, skeleton_rows)
    feature_pool = deduplicate_features(feature_rows)
    features_by_source = group_features_by_source(feature_rows)
    retriever = LexicalRetriever(
        build_fragment_corpus(
            skeleton_rows,
            feature_pool,
            extra_fragments=extra_fragment_rows,
        )
    )

    rows: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for base in base_rows:
        task_id = str(base.get("query_id") or "").strip()
        skeleton = skeleton_by_query_id[task_id]
        source_id = str(skeleton.get("source_id") or "").strip()
        source_features = features_by_source.get(source_id, [])
        if len(source_features) < feature_count:
            excluded.append(
                {
                    "query_id": task_id,
                    "source_id": source_id,
                    "reason": "insufficient_unique_features_in_seed_report",
                    "available_unique_features": len(source_features),
                    "required_unique_features": feature_count,
                }
            )
            continue

        positive_features = [
            feature
            for feature in source_features
            if score_skeleton_feature_affinity(skeleton, feature) > 0
        ]
        if len(positive_features) < feature_count:
            excluded.append(
                {
                    "query_id": task_id,
                    "source_id": source_id,
                    "reason": (
                        "insufficient_positive_affinity_features_in_seed_report"
                    ),
                    "available_unique_features": len(source_features),
                    "available_positive_affinity_features": len(positive_features),
                    "required_unique_features": feature_count,
                }
            )
            continue

        selected_features = select_feature_records(
            skeleton,
            query_features=[],
            all_features=source_features,
            mode="random_constrained",
            sample_size=feature_count,
            seed=seed,
            rng_salt="within-report",
        )
        retrieval_query = build_feature_only_retrieval_query(
            task_id, selected_features
        )
        fragment_groups = group_fragments(
            retriever.retrieve(
                retrieval_query,
                top_k=top_k,
                exclude_query_id=task_id,
            ),
            per_group_k=per_group_k,
        )
        row = _feature_only_row(
            base,
            selected_features,
            retrieval_query,
            fragment_groups,
            feature_selection="within_report_random_constrained",
            seed=seed,
        )
        selected_source_ids = sorted(
            {
                str(feature.get("source_id") or "").strip()
                for feature in selected_features
                if str(feature.get("source_id") or "").strip()
            }
        )
        row.update(
            {
                "selected_skeleton_id": skeleton.get("skeleton_id"),
                "guidance_skeleton_text": str(
                    skeleton.get("skeleton_text") or ""
                ),
                "generation_strategy": "feature-driven-soft",
                "composition_scope": "within_report_features",
                "same_report_source_id": source_id,
                "selected_feature_source_ids": selected_source_ids,
            }
        )
        rows.append(row)
    return rows, excluded


def validate_within_report_records(
    rows: Sequence[Mapping[str, Any]],
    excluded: Sequence[Mapping[str, Any]],
    *,
    expected_input_tasks: int = EXPECTED_INPUT_TASKS,
    feature_count: int = 3,
) -> None:
    row_ids = [str(row.get("query_id") or "") for row in rows]
    excluded_ids = [str(row.get("query_id") or "") for row in excluded]
    all_ids = row_ids + excluded_ids
    if len(all_ids) != expected_input_tasks or len(set(all_ids)) != expected_input_tasks:
        raise ValueError(
            f"expected {expected_input_tasks} unique input tasks across eligible and excluded records"
        )
    for row in rows:
        task_id = str(row.get("query_id") or "")
        source_id = str(row.get("same_report_source_id") or "")
        feature_ids = [str(value) for value in row.get("selected_feature_ids", [])]
        feature_sources = [
            str(value) for value in row.get("selected_feature_source_ids", [])
        ]
        if len(feature_ids) != feature_count or len(set(feature_ids)) != feature_count:
            raise ValueError(f"within_report_skeleton/{task_id}: invalid features")
        if feature_sources != [source_id]:
            raise ValueError(
                f"within_report_skeleton/{task_id}: cross-report feature provenance"
            )
        if row.get("composition_scope") != "within_report_features":
            raise ValueError(
                f"within_report_skeleton/{task_id}: invalid composition scope"
            )
        if row.get("feature_selection") != "within_report_random_constrained":
            raise ValueError(
                f"within_report_skeleton/{task_id}: invalid feature selection"
            )
        if row.get("generation_strategy") != "feature-driven-soft":
            raise ValueError(
                f"within_report_skeleton/{task_id}: invalid generation strategy"
            )
        if not str(row.get("guidance_skeleton_text") or "").strip():
            raise ValueError(
                f"within_report_skeleton/{task_id}: missing skeleton guidance"
            )
        retrieval = row.get("retrieval_query") or {}
        if retrieval.get("structure_keywords") or retrieval.get("hole_needs"):
            raise ValueError(
                f"within_report_skeleton/{task_id}: skeleton leaked into RAG"
            )
        for group in row.get("fragment_groups", []) or []:
            for fragment in group.get("items", []) or []:
                if str(fragment.get("query_id") or "") == task_id:
                    raise ValueError(
                        f"within_report_skeleton/{task_id}: retrieved current-task fragment"
                    )
    for item in excluded:
        task_id = str(item.get("query_id") or "")
        available = int(item.get("available_unique_features", -1))
        required = int(item.get("required_unique_features", feature_count))
        reason = item.get("reason")
        if reason not in {
            "insufficient_unique_features_in_seed_report",
            "insufficient_positive_affinity_features_in_seed_report",
        }:
            raise ValueError(f"excluded/{task_id}: invalid exclusion reason")
        if reason == "insufficient_unique_features_in_seed_report" and available >= required:
            raise ValueError(f"excluded/{task_id}: eligible task was excluded")
        if reason == "insufficient_positive_affinity_features_in_seed_report":
            positive = int(item.get("available_positive_affinity_features", -1))
            if available < required or positive >= required:
                raise ValueError(f"excluded/{task_id}: invalid affinity exclusion")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build deterministic skeleton-guided inputs whose selected features "
            "all come from the seed skeleton's historical report."
        )
    )
    parser.add_argument("--base-inputs", required=True)
    parser.add_argument("--skeletons", required=True)
    parser.add_argument("--features", required=True)
    parser.add_argument("--extra-fragments", nargs="+", action="extend", default=[])
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--seed", type=int, default=20260921)
    parser.add_argument(
        "--expected-input-tasks", type=int, default=EXPECTED_INPUT_TASKS
    )
    parser.add_argument("--feature-count", type=int, default=3)
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--per-group-k", type=int, default=3)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    base_path = Path(args.base_inputs)
    skeleton_path = Path(args.skeletons)
    feature_path = Path(args.features)
    extra_paths = [Path(path) for path in args.extra_fragments]
    output_dir = Path(args.output_dir)
    lane_path = output_dir / "within_report_skeleton.jsonl"
    excluded_path = output_dir / "excluded_tasks.jsonl"
    manifest_path = output_dir / "input_manifest.json"
    target_paths = [lane_path, excluded_path, manifest_path]
    existing_outputs = [path for path in target_paths if path.exists()]
    if existing_outputs:
        rendered_paths = ", ".join(str(path) for path in existing_outputs)
        raise FileExistsError(
            f"refusing to overwrite existing outputs: {rendered_paths}"
        )

    base_rows = list(iter_jsonl(base_path))
    skeleton_rows = list(iter_jsonl(skeleton_path))
    feature_rows = list(iter_jsonl(feature_path))
    extra_fragment_rows = [
        row for path in extra_paths for row in iter_jsonl(path)
    ]
    rows, excluded = build_within_report_records(
        base_rows,
        skeleton_rows,
        feature_rows,
        extra_fragment_rows,
        seed=args.seed,
        feature_count=args.feature_count,
        top_k=args.top_k,
        per_group_k=args.per_group_k,
    )
    validate_within_report_records(
        rows,
        excluded,
        expected_input_tasks=args.expected_input_tasks,
        feature_count=args.feature_count,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(rows, lane_path)
    write_jsonl(excluded, excluded_path)
    manifest = {
        "base_inputs": {
            "path": str(base_path),
            "count": len(base_rows),
            "sha256": _sha256(base_path),
        },
        "skeletons": {
            "path": str(skeleton_path),
            "count": len(skeleton_rows),
            "sha256": _sha256(skeleton_path),
        },
        "features": {
            "path": str(feature_path),
            "count": len(feature_rows),
            "sha256": _sha256(feature_path),
        },
        "extra_fragments": [
            {
                "path": str(path),
                "count": sum(1 for _ in iter_jsonl(path)),
                "sha256": _sha256(path),
            }
            for path in extra_paths
        ],
        "input_task_count": len(base_rows),
        "eligible_task_count": len(rows),
        "excluded_task_count": len(excluded),
        "selection_scope": "same_report_as_seed_skeleton",
        "selection_rule": "cross_report_positive_affinity_rule",
        "retrieval_scope": "shared_global_corpus",
        "seed": args.seed,
        "feature_count": args.feature_count,
        "top_k": args.top_k,
        "per_group_k": args.per_group_k,
        "lane": {
            "path": str(lane_path),
            "count": len(rows),
            "sha256": _sha256(lane_path),
        },
        "excluded_tasks": {
            "path": str(excluded_path),
            "count": len(excluded),
            "sha256": _sha256(excluded_path),
        },
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
