from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

EXPECTED_TASKS = 212

from src.stage2.build_fragment_corpus import build_fragment_corpus
from src.stage2.feature_selection import select_feature_records, selected_feature_metadata
from src.stage2.group_fragments import group_fragments
from src.stage2.retrieval_query_builder import build_retrieval_query
from src.stage2.retriever import LexicalRetriever
from src.common.jsonl_utils import iter_jsonl, write_jsonl


def deduplicate_features(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for row in records:
        feature_id = str(row.get("feature_id") or "").strip()
        if feature_id and feature_id not in by_id:
            by_id[feature_id] = row
    return [by_id[key] for key in sorted(by_id)]


def select_random_features(
    features: Iterable[dict[str, Any]],
    task_id: str,
    sample_size: int,
    seed: int,
) -> list[dict[str, Any]]:
    pseudo_skeleton = {"query_id": task_id, "skeleton_text": "", "holes": []}
    return select_feature_records(
        pseudo_skeleton,
        query_features=[],
        all_features=list(features),
        mode="random",
        sample_size=sample_size,
        seed=seed,
    )


def build_feature_only_retrieval_query(
    task_id: str, selected_features: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    empty = {"query_id": task_id, "skeleton_text": "", "holes": []}
    return build_retrieval_query(empty, list(selected_features))


def _feature_items(features: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "feature_id": feature.get("feature_id"),
            "text": (
                feature.get("feature_text")
                or feature.get("text")
                or feature.get("feature_name")
                or feature.get("feature_description")
            ),
            "description": feature.get("feature_description"),
            "evidence": feature.get("evidence"),
            "tags": feature.get("tags", []),
        }
        for feature in features
    ]


def _require_skeletons(
    base_inputs: Sequence[dict[str, Any]], skeletons: Sequence[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    by_query_id: dict[str, dict[str, Any]] = {}
    for skeleton in skeletons:
        query_id = str(skeleton.get("query_id") or "").strip()
        if not query_id:
            continue
        if query_id in by_query_id:
            raise ValueError(f"duplicate skeleton for task {query_id}")
        by_query_id[query_id] = skeleton
    for base in base_inputs:
        query_id = str(base.get("query_id") or "").strip()
        if not query_id:
            raise ValueError("base input has no query_id")
        if query_id not in by_query_id:
            raise ValueError(f"missing skeleton for task {query_id}")
    return by_query_id


def _feature_only_row(
    base: Mapping[str, Any],
    selected_features: Sequence[dict[str, Any]],
    retrieval_query: Mapping[str, Any],
    fragment_groups: Sequence[dict[str, Any]],
    *,
    feature_selection: str,
    seed: int,
) -> dict[str, Any]:
    row = dict(base)
    row.update({
        "skeleton_text": "",
        "guidance_skeleton_text": "",
        "holes": [],
        "selected_skeleton_id": None,
        "generation_strategy": "feature-fragment-only",
        "feature_selection": feature_selection,
        "feature_selection_seed": seed,
        **selected_feature_metadata(selected_features),
        "feature_items": _feature_items(selected_features),
        "retrieval_backend": "lexical",
        "retrieval_query": copy.deepcopy(dict(retrieval_query)),
        "fragment_groups": copy.deepcopy(list(fragment_groups)),
    })
    return row


def _coerce_builder_inputs(
    base_inputs: Iterable[dict[str, Any]] | Mapping[str, Any],
    skeletons: Iterable[dict[str, Any]] | None,
    features: Iterable[dict[str, Any]] | None,
    extra_fragments: Iterable[dict[str, Any]] | None,
    seed: int,
    feature_count: int,
    top_k: int,
    per_group_k: int,
) -> tuple[
    list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]],
    list[dict[str, Any]], int, int, int, int,
]:
    if isinstance(base_inputs, Mapping):
        config = base_inputs
        base_inputs = config.get("base_inputs", [])
        skeletons = config.get("skeletons", skeletons)
        features = config.get("features", features)
        extra_fragments = config.get("extra_fragments", extra_fragments)
        seed = int(config.get("seed", seed))
        feature_count = int(config.get("feature_count", feature_count))
        top_k = int(config.get("top_k", top_k))
        per_group_k = int(config.get("per_group_k", per_group_k))
    if skeletons is None or features is None:
        raise ValueError("skeletons and features are required")
    return (
        list(base_inputs), list(skeletons), list(features), list(extra_fragments or []),
        seed, feature_count, top_k, per_group_k,
    )


def build_three_lane_records(
    base_inputs: Iterable[dict[str, Any]] | Mapping[str, Any],
    skeletons: Iterable[dict[str, Any]] | None = None,
    features: Iterable[dict[str, Any]] | None = None,
    extra_fragments: Iterable[dict[str, Any]] | None = None,
    *,
    seed: int = 20260809,
    feature_count: int = 3,
    top_k: int = 8,
    per_group_k: int = 3,
) -> dict[str, list[dict[str, Any]]]:
    (
        base_rows, skeleton_rows, feature_rows, extra_fragment_rows,
        seed, feature_count, top_k, per_group_k,
    ) = _coerce_builder_inputs(
        base_inputs, skeletons, features, extra_fragments,
        seed, feature_count, top_k, per_group_k,
    )
    skeleton_by_query_id = _require_skeletons(base_rows, skeleton_rows)
    feature_pool = deduplicate_features(feature_rows)
    if len(feature_pool) < feature_count:
        raise ValueError(
            f"feature pool has {len(feature_pool)} unique features; need {feature_count}"
        )

    retriever = LexicalRetriever(
        build_fragment_corpus(
            skeleton_rows, feature_pool, extra_fragments=extra_fragment_rows
        )
    )
    lanes: dict[str, list[dict[str, Any]]] = {
        "random_feature_only": [],
        "conditioned_feature_only": [],
        "skeleton_feature": [],
    }
    for base in base_rows:
        task_id = str(base.get("query_id") or "").strip()
        skeleton = skeleton_by_query_id[task_id]
        random_features = select_random_features(
            feature_pool, task_id, feature_count, seed
        )
        conditioned_features = select_feature_records(
            skeleton,
            query_features=[],
            all_features=feature_pool,
            mode="random_constrained",
            sample_size=feature_count,
            seed=seed,
        )

        random_query = build_feature_only_retrieval_query(task_id, random_features)
        conditioned_query = build_feature_only_retrieval_query(
            task_id, conditioned_features
        )
        random_groups = group_fragments(
            retriever.retrieve(random_query, top_k=top_k, exclude_query_id=task_id),
            per_group_k=per_group_k,
        )
        conditioned_groups = group_fragments(
            retriever.retrieve(
                conditioned_query, top_k=top_k, exclude_query_id=task_id
            ),
            per_group_k=per_group_k,
        )

        random_row = _feature_only_row(
            base, random_features, random_query, random_groups,
            feature_selection="random", seed=seed,
        )
        conditioned_row = _feature_only_row(
            base, conditioned_features, conditioned_query, conditioned_groups,
            feature_selection="random_constrained", seed=seed,
        )
        skeleton_row = copy.deepcopy(conditioned_row)
        skeleton_row.update({
            "selected_skeleton_id": skeleton.get("skeleton_id"),
            "guidance_skeleton_text": str(skeleton.get("skeleton_text") or ""),
            "generation_strategy": "feature-driven-soft",
        })

        lanes["random_feature_only"].append(random_row)
        lanes["conditioned_feature_only"].append(conditioned_row)
        lanes["skeleton_feature"].append(skeleton_row)
    return lanes


def validate_three_lane_records(
    lanes: Mapping[str, Sequence[Mapping[str, Any]]], expected_tasks: int = 212
) -> None:
    required = {
        "random_feature_only",
        "conditioned_feature_only",
        "skeleton_feature",
    }
    if set(lanes) != required:
        raise ValueError(f"unexpected lane set: {sorted(lanes)}")
    for lane, rows in lanes.items():
        ids = [str(row.get("query_id") or "") for row in rows]
        if len(rows) != expected_tasks or len(set(ids)) != expected_tasks:
            raise ValueError(f"{lane}: expected {expected_tasks} unique tasks")
        for row in rows:
            task_id = str(row.get("query_id") or "")
            feature_ids = list(row.get("selected_feature_ids") or [])
            if len(feature_ids) != 3 or len(set(feature_ids)) != 3:
                raise ValueError(f"{lane}/{task_id}: invalid features")
            retrieval = row.get("retrieval_query") or {}
            if retrieval.get("structure_keywords") or retrieval.get("hole_needs"):
                raise ValueError(f"{lane}/{task_id}: skeleton leaked into RAG")
            for group in row.get("fragment_groups", []) or []:
                for fragment in group.get("items", []) or []:
                    if str(fragment.get("query_id") or "") == task_id:
                        raise ValueError(
                            f"{lane}/{task_id}: retrieved current-task fragment"
                        )
            if lane in {"random_feature_only", "conditioned_feature_only"}:
                for field in (
                    "skeleton_text",
                    "guidance_skeleton_text",
                    "holes",
                    "selected_skeleton_id",
                ):
                    if row.get(field) not in ("", [], None):
                        raise ValueError(f"{lane}/{task_id}: skeleton field {field} is set")
            if lane == "random_feature_only":
                if row.get("feature_selection") != "random":
                    raise ValueError(f"{lane}/{task_id}: feature_selection must be random")
                if row.get("generation_strategy") != "feature-fragment-only":
                    raise ValueError(
                        f"{lane}/{task_id}: invalid generation strategy"
                    )
            if lane == "conditioned_feature_only":
                if row.get("feature_selection") != "random_constrained":
                    raise ValueError(
                        f"{lane}/{task_id}: feature_selection must be random_constrained"
                    )
                if row.get("generation_strategy") != "feature-fragment-only":
                    raise ValueError(
                        f"{lane}/{task_id}: invalid generation strategy"
                    )
            if lane == "skeleton_feature":
                if row.get("feature_selection") != "random_constrained":
                    raise ValueError(
                        f"{lane}/{task_id}: feature_selection must be random_constrained"
                    )
                if row.get("generation_strategy") != "feature-driven-soft":
                    raise ValueError(
                        f"{lane}/{task_id}: invalid generation strategy"
                    )
                if not str(row.get("guidance_skeleton_text") or "").strip():
                    raise ValueError(f"{lane}/{task_id}: missing skeleton guidance")
    conditioned = {
        str(row["query_id"]): row for row in lanes["conditioned_feature_only"]
    }
    for row in lanes["skeleton_feature"]:
        task_id = str(row.get("query_id") or "")
        peer = conditioned.get(task_id)
        if peer is None:
            raise ValueError(f"skeleton_feature/{task_id}: missing conditioned peer")
        for field in (
            "selected_feature_ids",
            "feature_items",
            "retrieval_query",
            "fragment_groups",
        ):
            if peer.get(field) != row.get(field):
                raise ValueError(f"C/S mismatch for {task_id}: {field}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build deterministic random, conditioned, and skeleton ablation inputs."
    )
    parser.add_argument("--base-inputs", required=True)
    parser.add_argument("--skeletons", required=True)
    parser.add_argument("--features", required=True)
    parser.add_argument("--extra-fragments", nargs="+", action="extend", default=[])
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--seed", type=int, default=20260809)
    parser.add_argument("--expected-tasks", type=int, default=EXPECTED_TASKS)
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
    target_paths = [
        output_dir / "random_feature_only.jsonl",
        output_dir / "conditioned_feature_only.jsonl",
        output_dir / "skeleton_feature.jsonl",
        output_dir / "input_manifest.json",
    ]
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
    lanes = build_three_lane_records(
        base_rows,
        skeleton_rows,
        feature_rows,
        extra_fragment_rows,
        seed=args.seed,
        feature_count=args.feature_count,
        top_k=args.top_k,
        per_group_k=args.per_group_k,
    )
    validate_three_lane_records(lanes, expected_tasks=args.expected_tasks)

    output_dir.mkdir(parents=True, exist_ok=True)
    lane_manifest: dict[str, dict[str, Any]] = {}
    for lane, rows in lanes.items():
        output_path = output_dir / f"{lane}.jsonl"
        write_jsonl(rows, output_path)
        lane_manifest[lane] = {
            "path": str(output_path),
            "count": len(rows),
            "sha256": _sha256(output_path),
        }
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
        "feature_pool_size": len(deduplicate_features(feature_rows)),
        "expected_tasks": args.expected_tasks,
        "seed": args.seed,
        "feature_count": args.feature_count,
        "top_k": args.top_k,
        "per_group_k": args.per_group_k,
        "lanes": lane_manifest,
    }
    manifest_path = output_dir / "input_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
