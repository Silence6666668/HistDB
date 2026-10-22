from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.stage3e_diversity.metrics import (
    detect_semantic_features,
    feature_pairs,
    normalize_cypher_shape,
    semantic_mechanism_features,
)


CLAUSE_RE = re.compile(
    r"\b(?:OPTIONAL\s+MATCH|MATCH|CREATE|MERGE|WITH|WHERE|FILTER|UNWIND|CALL|RETURN|SET|DELETE|REMOVE|FOREACH|UNION|ORDER\s+BY|SKIP|LIMIT|NEXT|LET)\b",
    re.IGNORECASE,
)


def query_metrics(row: dict[str, Any]) -> dict[str, Any]:
    query = str(row.get("main_query") or row.get("query") or "")
    features = sorted(detect_semantic_features(query))
    pairs = sorted(feature_pairs(features))
    core = sorted(semantic_mechanism_features(features))
    core_pairs = sorted(feature_pairs(core))
    return {
        **row,
        "normalized_shape": normalize_cypher_shape(query),
        "realized_features": features,
        "realized_feature_pairs": [list(pair) for pair in pairs],
        "core_mechanism_features": core,
        "core_mechanism_pairs": [list(pair) for pair in core_pairs],
        "query_chars": len(query),
        "clause_count": len(CLAUSE_RE.findall(query)),
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    query_texts = {row.get("query", "") for row in rows}
    shapes = {row["normalized_shape"] for row in rows}
    feature_sets = {tuple(row["realized_features"]) for row in rows}
    feature_pair_set = {
        tuple(pair) for row in rows for pair in row["realized_feature_pairs"]
    }
    core_sets = {tuple(row["core_mechanism_features"]) for row in rows}
    core_pair_set = {
        tuple(pair) for row in rows for pair in row["core_mechanism_pairs"]
    }
    feature_freq = Counter(f for row in rows for f in row["realized_features"])
    clause_counts = [row["clause_count"] for row in rows]
    char_counts = [row["query_chars"] for row in rows]
    return {
        "queries": n,
        "unique_query_texts": len(query_texts),
        "duplicate_query_texts": n - len(query_texts),
        "unique_normalized_shapes": len(shapes),
        "normalized_shape_unique_rate": len(shapes) / n if n else None,
        "unique_realized_feature_sets": len(feature_sets),
        "unique_realized_feature_pairs": len(feature_pair_set),
        "unique_core_mechanism_sets": len(core_sets),
        "unique_core_mechanism_pairs": len(core_pair_set),
        "mean_query_chars": sum(char_counts) / n if n else None,
        "median_query_chars": sorted(char_counts)[n // 2] if n else None,
        "mean_clause_count": sum(clause_counts) / n if n else None,
        "median_clause_count": sorted(clause_counts)[n // 2] if n else None,
        "max_clause_count": max(clause_counts) if n else None,
        "top_features": feature_freq.most_common(25),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        default="paper/baseline_query_samples/memgraph_tool_query_samples_1000_each_20260813.jsonl",
    )
    parser.add_argument(
        "--output-dir",
        default="data/tool_query_sample_metrics_20260813",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with input_path.open(encoding="utf-8-sig") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            tool = str(row.get("tool") or "UNKNOWN")
            grouped[tool].append(query_metrics(row))

    summary = {
        "input": str(input_path),
        "tools": {tool: summarize(rows) for tool, rows in sorted(grouped.items())},
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    for tool, rows in sorted(grouped.items()):
        out = output_dir / f"{tool.lower()}_metrics.jsonl"
        with out.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    lines = [
        "# Tool Query Sample Metrics",
        "",
        f"Input: `{input_path}`",
        "",
        "| Tool | Queries | Unique text | Duplicates | Unique shapes | Feature sets | Feature pairs | Core sets | Core pairs | Mean chars | Mean clauses | Max clauses |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for tool, item in summary["tools"].items():
        lines.append(
            "| {tool} | {queries} | {unique_text} | {duplicates} | {shapes} | {sets} | {pairs} | {core_sets} | {core_pairs} | {chars:.2f} | {clauses:.2f} | {max_clauses} |".format(
                tool=tool,
                queries=item["queries"],
                unique_text=item["unique_query_texts"],
                duplicates=item["duplicate_query_texts"],
                shapes=item["unique_normalized_shapes"],
                sets=item["unique_realized_feature_sets"],
                pairs=item["unique_realized_feature_pairs"],
                core_sets=item["unique_core_mechanism_sets"],
                core_pairs=item["unique_core_mechanism_pairs"],
                chars=item["mean_query_chars"] or 0,
                clauses=item["mean_clause_count"] or 0,
                max_clauses=item["max_clause_count"] or 0,
            )
        )

    lines.extend(["", "## Top Features", ""])
    for tool, item in summary["tools"].items():
        tops = ", ".join(f"{name}:{count}" for name, count in item["top_features"][:12])
        lines.append(f"- {tool}: {tops}")

    (output_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(output_dir / "summary.md")


if __name__ == "__main__":
    main()
