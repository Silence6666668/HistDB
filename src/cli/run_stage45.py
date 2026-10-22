from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.common.io_utils import dump_json, ensure_dir
from src.common.jsonl_utils import iter_jsonl, write_jsonl
from src.stage4.compare import compare_execution_results, summarize_comparisons


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Stage 4.5: compare execution results across DB targets")
    parser.add_argument("--input", default="data/stage4/execution_results.jsonl")
    parser.add_argument("--output-dir", default="data/stage45")
    parser.add_argument("--targets", nargs="*", default=None, help="Optional expected target names for per-case comparison")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    if not input_path.exists():
        raise FileNotFoundError(f"Stage 4.5 input not found: {input_path}")

    rows = list(iter_jsonl(input_path))
    comparisons = compare_execution_results(rows, expected_targets=args.targets)

    output_dir = Path(args.output_dir)
    ensure_dir(output_dir)
    write_jsonl(comparisons, output_dir / "comparison_results.jsonl")

    summary = {
        "input_path": str(input_path),
        "output_dir": str(output_dir),
        "targets": args.targets or [],
        **summarize_comparisons(comparisons),
    }
    dump_json(summary, output_dir / "summary.json", pretty=True)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
