#!/usr/bin/env python3
"""Summarize diversity over repeated or disjoint query samples."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analyze_tool_query_sample_metrics import query_metrics, summarize


def aggregate_run_metrics(runs: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Return arithmetic means and sample standard deviations."""
    if not runs:
        return {}
    keys = sorted(set.intersection(*(set(run) for run in runs)))
    aggregated: dict[str, dict[str, float]] = {}
    for key in keys:
        values = [run[key] for run in runs]
        if not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in values):
            continue
        numeric = [float(value) for value in values]
        mean_value = statistics.fmean(numeric)
        sd_value = statistics.stdev(numeric) if len(numeric) > 1 else 0.0
        aggregated[key] = {
            "mean": int(mean_value) if mean_value.is_integer() else mean_value,
            "sd": int(sd_value) if sd_value.is_integer() else sd_value,
        }
    return aggregated


def load_grouped_runs(path: Path) -> dict[str, dict[int, list[dict[str, Any]]]]:
    grouped: dict[str, dict[int, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    with path.open(encoding="utf-8-sig") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            tool = str(row.get("tool") or "UNKNOWN")
            run = int(row.get("run") or 1)
            grouped[tool][run].append(query_metrics(row))
    return grouped


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--design",
        default="repeated samples; consult the input manifest for independence",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    grouped = load_grouped_runs(input_path)

    result: dict[str, Any] = {
        "input": str(input_path),
        "design": args.design,
        "tools": {},
    }
    for tool, runs in sorted(grouped.items()):
        per_run = [
            {"run": run, **summarize(rows)}
            for run, rows in sorted(runs.items())
        ]
        result["tools"][tool] = {
            "run_count": len(per_run),
            "per_run": per_run,
            "aggregate": aggregate_run_metrics(per_run),
        }

    (output_dir / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    metrics = [
        ("queries", "Queries"),
        ("unique_query_texts", "Unique text"),
        ("duplicate_query_texts", "Duplicates"),
        ("unique_normalized_shapes", "Unique shapes"),
        ("unique_realized_feature_sets", "Feature sets"),
        ("unique_realized_feature_pairs", "Feature pairs"),
        ("unique_core_mechanism_sets", "Core sets"),
        ("unique_core_mechanism_pairs", "Core pairs"),
        ("mean_query_chars", "Mean chars"),
        ("median_query_chars", "Median chars"),
        ("mean_clause_count", "Mean clauses"),
        ("median_clause_count", "Median clauses"),
        ("max_clause_count", "Max clauses"),
    ]
    lines = [
        "# Repeated Query-Diversity Analysis",
        "",
        f"Design: {args.design}",
        "",
        "Values are arithmetic mean +/- sample standard deviation across the retained runs or samples.",
        "",
        "| Tool | Runs | " + " | ".join(label for _, label in metrics) + " |",
        "|---|---:|" + "---:|" * len(metrics),
    ]
    for tool, item in result["tools"].items():
        values = []
        for key, _ in metrics:
            aggregate = item["aggregate"].get(key, {"mean": 0, "sd": 0})
            values.append(f"{aggregate['mean']:.1f} +/- {aggregate['sd']:.1f}")
        lines.append(f"| {tool} | {item['run_count']} | " + " | ".join(values) + " |")

    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- Each run is a disjoint 1,000-query sample per tool, not a separate 24-hour tool execution.",
            "- Rows are normalized into the project case schema with `case_id`, `query_id`, `main_query`, `setup_queries`, and `cleanup_queries` before feature extraction.",
            "- The same feature detector is used for every tool via `scripts/analyze_tool_query_sample_metrics.py` and `src/stage3e_diversity/metrics.py`.",
        ]
    )
    (output_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(output_dir / "summary.json")
    print(output_dir / "summary.md")


if __name__ == "__main__":
    main()
