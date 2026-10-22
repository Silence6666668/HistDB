from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Set

from src.common.config import load_yaml
from src.common.io_utils import dump_json, ensure_dir
from src.common.jsonl_utils import append_jsonl, iter_jsonl
from src.stage4.executor import (
    execute_case_against_target,
    execute_case_against_target_with_hard_timeout,
    load_db_targets,
)
from src.stage4.executor_age import execute_case_against_target_age
from src.stage4.executor_arcadedb import execute_case_against_target_arcadedb
from src.stage4.executor_kuzu import execute_case_against_target_kuzu


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Stage 4b: execute metamorphic original/rewrite pairs on one DB target")
    parser.add_argument("--input", default="data/stage3b_rewrites/selfdiff_cases.jsonl")
    parser.add_argument("--db-config", default="configs/db_targets.yaml")
    parser.add_argument("--output-dir", default="data/stage4b_selfdiff")
    parser.add_argument("--targets", nargs="*", default=["neo4j_docker"], help="Single-DB self-diff targets")
    parser.add_argument("--max-items", type=int, default=None)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--skip-cleanup", action="store_true")
    parser.add_argument("--query-timeout-sec", type=float, default=10.0)
    return parser.parse_args()


def _load_existing_keys(path: Path) -> Set[str]:
    if not path.exists():
        return set()
    keys: Set[str] = set()
    for record in iter_jsonl(path):
        pair_id = str(record.get("selfdiff_pair_id") or "")
        variant = str(record.get("selfdiff_variant") or "")
        target = str(record.get("target") or "")
        if pair_id and variant and target:
            keys.add(f"{pair_id}::{variant}::{target}")
    return keys


def _run_case(case: Dict[str, Any], target: Any, run_cleanup: bool) -> Dict[str, Any]:
    if target.engine == "age":
        result = execute_case_against_target_age(case, target, run_cleanup=run_cleanup)
    elif target.engine == "arcadedb":
        result = execute_case_against_target_arcadedb(case, target, run_cleanup=run_cleanup)
    elif target.engine == "kuzu":
        result = execute_case_against_target_kuzu(case, target, run_cleanup=run_cleanup)
    elif target.engine == "bolt":
        result = execute_case_against_target_with_hard_timeout(case, target, run_cleanup=run_cleanup)
    else:
        result = execute_case_against_target(case, target, run_cleanup=run_cleanup)
    result["selfdiff_pair_id"] = case.get("selfdiff_pair_id")
    result["selfdiff_variant"] = case.get("selfdiff_variant")
    result["rewrite_type"] = case.get("rewrite_type")
    result["original_case_id"] = case.get("original_case_id")
    result["original_query_id"] = case.get("original_query_id")
    return result


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    if not input_path.exists():
        raise FileNotFoundError(f"Stage 4b input not found: {input_path}")

    db_cfg = load_yaml(args.db_config)
    targets = load_db_targets(db_cfg, selected_names=args.targets)
    if not targets:
        raise ValueError(f"No enabled DB targets found in {args.db_config}")

    for target in targets:
        target.query_timeout_sec = args.query_timeout_sec

    output_dir = Path(args.output_dir)
    ensure_dir(output_dir)
    results_path = output_dir / "execution_results.jsonl"
    existing = _load_existing_keys(results_path) if args.resume else set()
    if not args.resume and results_path.exists():
        results_path.unlink()

    processed = 0
    status_counter: Counter[str] = Counter()
    target_counter: Counter[str] = Counter()
    variant_counter: Counter[str] = Counter()

    for case in iter_jsonl(input_path):
        if args.max_items is not None and processed >= args.max_items:
            break
        for target in targets:
            pair_key = f"{case.get('selfdiff_pair_id')}::{case.get('selfdiff_variant')}::{target.name}"
            if pair_key in existing:
                continue
            result = _run_case(case, target, run_cleanup=not args.skip_cleanup)
            append_jsonl(result, results_path)
            processed += 1
            status_counter[result["status"]] += 1
            target_counter[target.name] += 1
            variant_counter[str(result.get("selfdiff_variant") or "unknown")] += 1
            print(
                f"[Stage4b] processed {processed}: pair={case.get('selfdiff_pair_id')} | "
                f"variant={case.get('selfdiff_variant')} | target={target.name} | status={result['status']}"
            )

    summary: Dict[str, Any] = {
        "input_path": str(input_path),
        "db_config": args.db_config,
        "output_dir": str(output_dir),
        "targets": [target.name for target in targets],
        "processed_variant_target_runs": processed,
        "status_counts": dict(status_counter),
        "target_counts": dict(target_counter),
        "variant_counts": dict(variant_counter),
        "skip_cleanup": bool(args.skip_cleanup),
        "query_timeout_sec": args.query_timeout_sec,
    }
    dump_json(summary, output_dir / "summary.json", pretty=True)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
