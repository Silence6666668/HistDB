from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Set

from src.common.config import load_yaml
from src.common.io_utils import dump_json, ensure_dir
from src.common.jsonl_utils import append_jsonl, iter_jsonl
from src.stage4.executor_agensgraph import execute_case_against_target_agensgraph
from src.stage4.executor_arcadedb import execute_case_against_target_arcadedb
from src.stage4.executor_falkordb import execute_case_against_target_falkordb
from src.stage4.executor_memgraph_cli import execute_case_against_target_memgraph_cli
from src.stage4.executor_neo4j_http import execute_case_against_target_neo4j_http
from src.stage4.executor import (
    execute_case_against_target,
    execute_case_against_target_with_hard_timeout,
    load_db_targets,
)
from src.stage4.executor_age import execute_case_against_target_age


def execute_case_for_target(
    case: Dict[str, Any],
    target: Any,
    *,
    run_cleanup: bool = True,
) -> Dict[str, Any]:
    """Dispatch one case through the canonical Stage 4 engine mapping."""
    if target.engine == "age":
        return execute_case_against_target_age(case, target, run_cleanup=run_cleanup)
    if target.engine == "agensgraph":
        return execute_case_against_target_agensgraph(case, target, run_cleanup=run_cleanup)
    if target.engine == "arcadedb":
        return execute_case_against_target_arcadedb(case, target, run_cleanup=run_cleanup)
    if target.engine == "falkordb":
        return execute_case_against_target_falkordb(case, target, run_cleanup=run_cleanup)
    if target.engine == "memgraph_cli":
        return execute_case_against_target_memgraph_cli(
            case, target, run_cleanup=run_cleanup
        )
    if target.engine == "neo4j_http":
        return execute_case_against_target_neo4j_http(
            case, target, run_cleanup=run_cleanup
        )
    if target.engine == "kuzu":
        from src.stage4.executor_kuzu import execute_case_against_target_kuzu

        return execute_case_against_target_kuzu(case, target, run_cleanup=run_cleanup)
    return execute_case_against_target_with_hard_timeout(
        case,
        target,
        run_cleanup=run_cleanup,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Stage 4: execute testcases against graph databases")
    parser.add_argument("--input", default="data/stage3_llm_v2/instantiated_cases.jsonl")
    parser.add_argument("--db-config", default="configs/db_targets.yaml")
    parser.add_argument("--output-dir", default="data/stage4")
    parser.add_argument("--targets", nargs="*", default=None, help="Optional target names to run")
    parser.add_argument("--max-items", type=int, default=None)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--skip-cleanup", action="store_true")
    parser.add_argument("--query-timeout-sec", type=float, default=10.0)
    return parser.parse_args()


def _load_existing_case_target_pairs(path: Path) -> Set[str]:
    if not path.exists():
        return set()
    pairs: Set[str] = set()
    for record in iter_jsonl(path):
        query_id = str(record.get("query_id") or "")
        case_id = str(record.get("case_id") or "")
        target = str(record.get("target") or "")
        if query_id and case_id and target:
            pairs.add(f"{query_id}::{case_id}::{target}")
    return pairs


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    if not input_path.exists():
        raise FileNotFoundError(f"Stage 4 input not found: {input_path}")

    db_cfg = load_yaml(args.db_config)
    targets = load_db_targets(db_cfg, selected_names=args.targets)
    if not targets:
        raise ValueError(f"No enabled DB targets found in {args.db_config}")
    if args.query_timeout_sec is not None:
        for target in targets:
            target.query_timeout_sec = args.query_timeout_sec

    output_dir = Path(args.output_dir)
    ensure_dir(output_dir)
    results_path = output_dir / "execution_results.jsonl"
    existing = _load_existing_case_target_pairs(results_path) if args.resume else set()
    if not args.resume and results_path.exists():
        results_path.unlink()

    processed = 0
    status_counter: Counter[str] = Counter()
    target_counter: Counter[str] = Counter()

    for case in iter_jsonl(input_path):
        if args.max_items is not None and processed >= args.max_items:
            break
        for target in targets:
            pair_key = f"{case.get('query_id')}::{case.get('case_id')}::{target.name}"
            if pair_key in existing:
                continue
            result = execute_case_for_target(
                case,
                target,
                run_cleanup=not args.skip_cleanup,
            )
            append_jsonl(result, results_path)
            processed += 1
            status_counter[result["status"]] += 1
            target_counter[target.name] += 1
            print(
                f"[Stage4] processed {processed}: {case.get('query_id')} | "
                f"target={target.name} | status={result['status']}"
            )

    summary: Dict[str, Any] = {
        "input_path": str(input_path),
        "db_config": args.db_config,
        "output_dir": str(output_dir),
        "targets": [target.name for target in targets],
        "processed_case_target_runs": processed,
        "status_counts": dict(status_counter),
        "target_counts": dict(target_counter),
        "skip_cleanup": bool(args.skip_cleanup),
        "query_timeout_sec": args.query_timeout_sec,
    }
    dump_json(summary, output_dir / "summary.json", pretty=True)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
