#!/usr/bin/env python3
"""Summarize one or more JaCoCo CSV reports.

Example:
  python scripts/summarize_jacoco_csv.py \
    --input ours=coverage_runs/ours/jacoco_report/jacoco.csv \
    --input gdsmith=coverage_runs/gdsmith/jacoco_report/jacoco.csv \
    --output-csv paper/coverage_summary.csv \
    --output-md paper/coverage_summary.md
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


COUNTER_PAIRS = {
    "instruction": ("INSTRUCTION_MISSED", "INSTRUCTION_COVERED"),
    "branch": ("BRANCH_MISSED", "BRANCH_COVERED"),
    "line": ("LINE_MISSED", "LINE_COVERED"),
    "complexity": ("COMPLEXITY_MISSED", "COMPLEXITY_COVERED"),
    "method": ("METHOD_MISSED", "METHOD_COVERED"),
}


def parse_input(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("--input must be LABEL=PATH")
    label, path = value.split("=", 1)
    label = label.strip()
    if not label:
        raise argparse.ArgumentTypeError("input label cannot be empty")
    return label, Path(path)


def pct(covered: int, missed: int) -> float:
    total = covered + missed
    if total == 0:
        return 0.0
    return covered * 100.0 / total


def summarize(label: str, path: Path) -> dict[str, object]:
    totals = {name: {"missed": 0, "covered": 0} for name in COUNTER_PAIRS}
    packages: set[str] = set()
    classes = 0
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            classes += 1
            packages.add(row.get("PACKAGE", ""))
            for name, (missed_col, covered_col) in COUNTER_PAIRS.items():
                totals[name]["missed"] += int(row.get(missed_col, 0) or 0)
                totals[name]["covered"] += int(row.get(covered_col, 0) or 0)

    out: dict[str, object] = {
        "label": label,
        "path": str(path),
        "classes": classes,
        "packages": len(packages),
    }
    for name, counts in totals.items():
        missed = int(counts["missed"])
        covered = int(counts["covered"])
        out[f"{name}_missed"] = missed
        out[f"{name}_covered"] = covered
        out[f"{name}_pct"] = pct(covered, missed)
    return out


def write_csv(rows: list[dict[str, object]], path: Path) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_md(rows: list[dict[str, object]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    headers = [
        "Label",
        "Classes",
        "Instr. %",
        "Branch %",
        "Line %",
        "Method %",
        "Covered Instr.",
        "Covered Lines",
    ]
    lines = [
        "# JaCoCo Coverage Summary",
        "",
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] + ["---:"] * (len(headers) - 1)) + " |",
    ]
    for row in rows:
        lines.append(
            "| {label} | {classes} | {instruction_pct:.2f} | {branch_pct:.2f} | "
            "{line_pct:.2f} | {method_pct:.2f} | {instruction_covered} | "
            "{line_covered} |".format(**row)
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", action="append", type=parse_input, required=True)
    parser.add_argument("--output-csv", type=Path)
    parser.add_argument("--output-md", type=Path)
    args = parser.parse_args()

    rows = [summarize(label, path) for label, path in args.input]
    if args.output_csv:
        write_csv(rows, args.output_csv)
    if args.output_md:
        write_md(rows, args.output_md)

    for row in rows:
        print(
            f"{row['label']}: instruction={row['instruction_pct']:.2f}% "
            f"branch={row['branch_pct']:.2f}% line={row['line_pct']:.2f}%"
        )


if __name__ == "__main__":
    main()
