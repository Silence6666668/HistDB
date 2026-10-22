from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Set

from src.common.config import load_yaml
from src.common.io_utils import dump_json, ensure_dir
from src.common.jsonl_utils import append_jsonl, iter_jsonl, write_jsonl
from src.common.text_utils import first_nonempty
from src.llm.model_client import create_model_client, resolve_model_config
from src.stage1.build_pools import build_feature_pool, build_skeleton_pool
from src.stage1.extract_features import (
    heuristic_extract_feature_items,
    llm_extract_feature_items,
    load_feature_prompt,
    stable_feature_id,
)
from src.stage1.extract_skeletons import (
    heuristic_extract_skeleton,
    llm_extract_skeleton,
    load_system_prompt,
    stable_skeleton_id,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Stage 1: skeleton + feature extraction")
    parser.add_argument("--pipeline-config", default="configs/pipeline.yaml")
    parser.add_argument("--model-config", default="configs/model.yaml")
    parser.add_argument("--input", default=None, help="Override input prepared_queries.jsonl")
    parser.add_argument("--output-dir", default=None, help="Override stage1 output dir")
    parser.add_argument("--max-items", type=int, default=None)
    parser.add_argument("--skeleton-mode", choices=["llm", "heuristic"], default=None)
    parser.add_argument("--feature-mode", choices=["llm", "heuristic"], default=None)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--skeleton-prompt", default="prompts/extract_skeleton_system.txt")
    parser.add_argument("--feature-prompt", default="prompts/extract_feature_system.txt")
    return parser.parse_args()


def resolve_query_text(record: Dict[str, Any]) -> str | None:
    return first_nonempty(
        record.get("query_text"),
        record.get("normalized_query"),
        record.get("query"),
        record.get("text"),
    )


def _load_completed_queries(path: Path) -> Set[str]:
    if not path.exists():
        return set()
    completed: Set[str] = set()
    for rec in iter_jsonl(path):
        query_id = str(rec.get("query_id") or "").strip()
        if query_id:
            completed.add(query_id)
    return completed


def _rewrite_records_for_completed_queries(path: Path, completed: Set[str]) -> None:
    if not path.exists():
        return
    kept: List[Dict[str, Any]] = []
    for rec in iter_jsonl(path):
        query_id = str(rec.get("query_id") or "").strip()
        if query_id in completed:
            kept.append(rec)
    write_jsonl(kept, path)


def main() -> int:
    args = parse_args()
    pipe_cfg = load_yaml(args.pipeline_config)
    model_cfg = load_yaml(args.model_config)

    input_path = Path(args.input or pipe_cfg.get("paths", {}).get("input_queries", "data/queries/prepared_queries.jsonl"))
    output_dir = Path(args.output_dir or pipe_cfg.get("paths", {}).get("output_dir", "data/stage1"))
    stage1_cfg = pipe_cfg.get("stage1", {})
    skeleton_mode = args.skeleton_mode or stage1_cfg.get("skeleton_mode", "llm")
    feature_mode = args.feature_mode or stage1_cfg.get("feature_mode", "llm")
    max_items = args.max_items if args.max_items is not None else stage1_cfg.get("max_items")
    save_pretty_json = bool(stage1_cfg.get("save_pretty_json", True))
    resume = bool(args.resume)

    ensure_dir(output_dir)

    if not input_path.exists():
        raise FileNotFoundError(f"Input JSONL not found: {input_path}")

    skeleton_prompt = load_system_prompt(args.skeleton_prompt)
    feature_prompt = load_feature_prompt(args.feature_prompt)
    skeleton_client = create_model_client(model_cfg, task="skeleton") if skeleton_mode == "llm" else None
    feature_client = create_model_client(model_cfg, task="feature") if feature_mode == "llm" else None

    skeletons_path = output_dir / "skeletons.jsonl"
    features_path = output_dir / "features.jsonl"
    skeleton_pool_path = output_dir / "skeleton_pool.jsonl"
    feature_pool_path = output_dir / "feature_pool.jsonl"
    skipped_path = output_dir / "skipped.jsonl"
    completed_path = output_dir / "completed_queries.jsonl"
    summary_path = output_dir / "summary.json"

    if not resume:
        for path in (
            skeletons_path,
            features_path,
            skeleton_pool_path,
            feature_pool_path,
            skipped_path,
            completed_path,
            summary_path,
        ):
            if path.exists():
                path.unlink()

    completed_queries = _load_completed_queries(completed_path) if resume else set()
    if resume:
        _rewrite_records_for_completed_queries(skeletons_path, completed_queries)
        _rewrite_records_for_completed_queries(features_path, completed_queries)
        _rewrite_records_for_completed_queries(skipped_path, completed_queries)

    for idx, record in enumerate(iter_jsonl(input_path), start=1):
        if max_items is not None and len(completed_queries) >= max_items:
            break

        query_text = resolve_query_text(record)
        query_id = str(record.get("query_id") or f"q_{idx:06d}")
        source_id = str(record.get("source_id") or record.get("issue_id") or "unknown")
        prepared_source_name = str(record.get("prepared_source_name") or "").strip() or None
        prepared_source_path = str(record.get("prepared_source_path") or "").strip() or None

        if resume and query_id in completed_queries:
            continue

        if not query_text:
            skipped_record = {
                "query_id": query_id,
                "source_id": source_id,
                "prepared_source_name": prepared_source_name,
                "prepared_source_path": prepared_source_path,
                "reason": "missing_query_text",
            }
            append_jsonl(skipped_record, skipped_path)
            append_jsonl({"query_id": query_id}, completed_path)
            completed_queries.add(query_id)
            continue

        try:
            if skeleton_mode == "llm":
                sk_result = llm_extract_skeleton(query_text=query_text, client=skeleton_client, system_prompt=skeleton_prompt)  # type: ignore[arg-type]
            else:
                sk_result = heuristic_extract_skeleton(query_text)
        except Exception as e:
            sk_result = heuristic_extract_skeleton(query_text)
            sk_result.notes = f"fallback_from_llm_error: {e}"

        skeleton_id = stable_skeleton_id(sk_result.skeleton_text)
        skeleton_record = {
            "query_id": query_id,
            "source_id": source_id,
            "repo": record.get("repo"),
            "issue_number": record.get("issue_number"),
            "url": record.get("url"),
            "prepared_source_name": prepared_source_name,
            "prepared_source_path": prepared_source_path,
            "query_text": query_text,
            "skeleton_id": skeleton_id,
            "skeleton_text": sk_result.skeleton_text,
            "holes": sk_result.holes,
            "method": sk_result.method,
            "notes": sk_result.notes,
        }
        append_jsonl(skeleton_record, skeletons_path)

        try:
            if feature_mode == "llm":
                ft_result = llm_extract_feature_items(query_text=query_text, client=feature_client, system_prompt=feature_prompt)  # type: ignore[arg-type]
            else:
                ft_result = heuristic_extract_feature_items(query_text)
        except Exception as e:
            ft_result = heuristic_extract_feature_items(query_text)
            ft_result.notes = f"fallback_from_llm_error: {e}"

        for pos, item in enumerate(ft_result.feature_items, start=1):
            feature_name = str(item.get("feature_name") or f"feature_{pos}")
            feature_description = str(item.get("feature_description") or "")
            feature_record = {
                "feature_id": stable_feature_id(feature_name, feature_description),
                "query_id": query_id,
                "source_id": source_id,
                "repo": record.get("repo"),
                "issue_number": record.get("issue_number"),
                "url": record.get("url"),
                "prepared_source_name": prepared_source_name,
                "prepared_source_path": prepared_source_path,
                "feature_name": feature_name,
                "feature_description": feature_description,
                "query_instance": str(item.get("query_instance") or query_text),
                "tags": item.get("tags", []),
                "feature_signature": item.get("feature_signature", []),
                "flags": item.get("flags", {}),
                "entities": item.get("entities", {}),
                "method": ft_result.method,
                "notes": ft_result.notes,
            }
            append_jsonl(feature_record, features_path)

        append_jsonl({"query_id": query_id}, completed_path)
        completed_queries.add(query_id)
        print(f"[Stage1] processed {len(completed_queries)}: {query_id} -> {skeleton_id} | features={len(ft_result.feature_items)}")

    skeleton_records = list(iter_jsonl(skeletons_path)) if skeletons_path.exists() else []
    feature_records = list(iter_jsonl(features_path)) if features_path.exists() else []
    skipped_records = list(iter_jsonl(skipped_path)) if skipped_path.exists() else []

    skeleton_pool = build_skeleton_pool(skeleton_records)
    feature_pool = build_feature_pool(feature_records)

    write_jsonl(skeleton_pool, skeleton_pool_path)
    write_jsonl(feature_pool, feature_pool_path)

    summary = {
        "input_path": str(input_path),
        "output_dir": str(output_dir),
        "skeleton_mode": skeleton_mode,
        "feature_mode": feature_mode,
        "skeleton_model": resolve_model_config(model_cfg, task="skeleton").get("model") if skeleton_mode == "llm" else None,
        "feature_model": resolve_model_config(model_cfg, task="feature").get("model") if feature_mode == "llm" else None,
        "processed_queries": len(skeleton_records),
        "extracted_feature_items": len(feature_records),
        "skipped": len(skipped_records),
        "unique_skeletons": len(skeleton_pool),
        "feature_groups": len(feature_pool),
    }
    dump_json(summary, summary_path, pretty=save_pretty_json)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
