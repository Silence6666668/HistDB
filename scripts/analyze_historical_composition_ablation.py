#!/usr/bin/env python3
"""Analyze cross-report selection and historically unseen realized pairs.

The historical unit is a complete bug report (``source_id``), not an
individual query extracted from that report.  Selection-side metrics audit the
provenance of Stage2 inputs.  Realization-side metrics use only mechanisms
detected in accepted Stage3 ``main_query`` text; they do not claim that a
particular selected feature was realized.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.stage3e_diversity.metrics import (
    detect_semantic_features,
    feature_pairs,
    semantic_mechanism_features,
)


ARM_FILES = {
    "R": "random_feature_only.jsonl",
    "C": "conditioned_feature_only.jsonl",
    "S": "skeleton_feature.jsonl",
}
ARM_LABELS = {
    "R": "R: random feature-only",
    "C": "C: conditioned feature-only",
    "S": "S: cross-report skeleton-guided",
    "W": "W: within-report feature source",
}


def select_stratified_matched_accepted(
    rows: Sequence[dict[str, Any]],
    *,
    sample_size: int,
    sample_seed: int,
    arms: Sequence[str] = ("R", "C", "S"),
) -> dict[str, list[dict[str, Any]]]:
    """Select the same accepted task IDs for every arm, balanced by run seed."""

    if sample_size <= 0:
        raise ValueError("sample_size must be positive")
    arm_names = tuple(arms)
    if not arm_names:
        raise ValueError("At least one arm is required")

    indexed: dict[str, dict[tuple[int, str], dict[str, Any]]] = {
        arm: {} for arm in arm_names
    }
    for row in rows:
        arm = str(row.get("arm") or "")
        if arm not in indexed:
            continue
        key = (int(row["seed"]), str(row["query_id"]))
        if key in indexed[arm]:
            raise ValueError(f"Duplicate accepted case for arm {arm}: {key}")
        indexed[arm][key] = row

    common_keys = set.intersection(
        *(set(indexed[arm]) for arm in arm_names)
    )
    if len(common_keys) < sample_size:
        raise ValueError(
            f"Need {sample_size} matched accepted tasks, found {len(common_keys)}"
        )

    keys_by_seed: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for key in common_keys:
        keys_by_seed[key[0]].append(key)
    seeds = sorted(keys_by_seed)
    base, remainder = divmod(sample_size, len(seeds))
    quotas = {
        seed: base + int(index < remainder) for index, seed in enumerate(seeds)
    }

    selected_keys: list[tuple[int, str]] = []
    for seed in seeds:
        quota = quotas[seed]
        candidates = keys_by_seed[seed]
        if len(candidates) < quota:
            raise ValueError(
                f"Seed {seed} has {len(candidates)} matched accepted tasks; "
                f"needs {quota}"
            )
        ranked = sorted(
            candidates,
            key=lambda key: hashlib.sha256(
                f"{sample_seed}|{key[0]}|{key[1]}".encode("utf-8")
            ).hexdigest(),
        )
        selected_keys.extend(ranked[:quota])

    return {
        arm: [indexed[arm][key] for key in selected_keys] for arm in arm_names
    }


def analyze_matched_accepted_sample(
    accepted_rows: Sequence[dict[str, Any]],
    input_rows: dict[tuple[str, int], Sequence[dict[str, Any]]],
    history: dict[str, Any],
    *,
    sample_size: int,
    sample_seed: int,
    arms: Sequence[str] = ("R", "C", "S"),
) -> dict[str, Any]:
    """Analyze an equal-size, task-matched sample of accepted queries."""

    arm_names = tuple(arms)
    selected = select_stratified_matched_accepted(
        accepted_rows,
        sample_size=sample_size,
        sample_seed=sample_seed,
        arms=arm_names,
    )
    accepted_keys_by_arm = {
        arm: {
            (int(row["seed"]), str(row["query_id"]))
            for row in accepted_rows
            if str(row.get("arm") or "") == arm
        }
        for arm in arm_names
    }
    common_pool_size = len(
        set.intersection(*(accepted_keys_by_arm[arm] for arm in arm_names))
    )
    input_index: dict[str, dict[tuple[int, str], dict[str, Any]]] = {
        arm: {} for arm in arm_names
    }
    for (arm, seed), rows in input_rows.items():
        if arm not in input_index:
            continue
        for row in rows:
            key = (int(seed), str(row["query_id"]))
            if key in input_index[arm]:
                raise ValueError(f"Duplicate input task for arm {arm}: {key}")
            input_index[arm][key] = row

    selected_keys = [
        (int(row["seed"]), str(row["query_id"])) for row in selected[arm_names[0]]
    ]
    seed_quotas: dict[int, int] = defaultdict(int)
    for seed, _ in selected_keys:
        seed_quotas[seed] += 1

    result: dict[str, Any] = {
        "sampling": {
            "mode": "stratified task-matched accepted queries",
            "common_pool_size": common_pool_size,
            "sample_size_per_arm": sample_size,
            "sample_seed": sample_seed,
            "seed_quotas": dict(sorted(seed_quotas.items())),
        },
        "checks": {},
        "arms": {},
        "sample_keys": [
            {"sample_index": index, "seed": seed, "query_id": query_id}
            for index, (seed, query_id) in enumerate(selected_keys, start=1)
        ],
    }

    selected_inputs: dict[str, list[dict[str, Any]]] = {}
    for arm in arm_names:
        missing = [key for key in selected_keys if key not in input_index[arm]]
        if missing:
            raise ValueError(
                f"Missing {len(missing)} selected input tasks for arm {arm}; "
                f"first missing key: {missing[0]}"
            )
        selected_inputs[arm] = [input_index[arm][key] for key in selected_keys]
        metrics = {
            **selection_run_metrics(selected_inputs[arm], history),
            **realized_run_metrics(selected[arm], history),
        }
        result["arms"][arm] = {
            "label": ARM_LABELS.get(arm, arm),
            "metrics": metrics,
        }

    if "C" in selected_inputs and "S" in selected_inputs:
        result["checks"]["c_s_selected_feature_mismatches"] = sum(
            tuple(c_row.get("selected_feature_ids") or [])
            != tuple(s_row.get("selected_feature_ids") or [])
            for c_row, s_row in zip(selected_inputs["C"], selected_inputs["S"])
        )
    return result


def analyze_fixed_matched_sample(
    accepted_rows: Sequence[dict[str, Any]],
    input_rows: dict[tuple[str, int], Sequence[dict[str, Any]]],
    history: dict[str, Any],
    *,
    sample_keys: Sequence[tuple[int, str]],
    arms: Sequence[str] = ("R", "C", "S"),
) -> dict[str, Any]:
    """Analyze an already locked, task-matched retained-query cohort."""

    arm_names = tuple(arms)
    if not arm_names:
        raise ValueError("At least one arm is required")
    selected_keys = [(int(seed), str(query_id)) for seed, query_id in sample_keys]
    if not selected_keys:
        raise ValueError("sample_keys must not be empty")
    if len(selected_keys) != len(set(selected_keys)):
        raise ValueError("sample_keys contains duplicate task keys")

    accepted_index: dict[str, dict[tuple[int, str], dict[str, Any]]] = {
        arm: {} for arm in arm_names
    }
    for row in accepted_rows:
        arm = str(row.get("arm") or "")
        if arm not in accepted_index:
            continue
        key = (int(row["seed"]), str(row["query_id"]))
        if key in accepted_index[arm]:
            raise ValueError(f"Duplicate accepted case for arm {arm}: {key}")
        accepted_index[arm][key] = row

    for arm in arm_names:
        missing = [key for key in selected_keys if key not in accepted_index[arm]]
        if missing:
            raise ValueError(
                f"Missing {len(missing)} locked accepted tasks for arm {arm}; "
                f"first missing key: {missing[0]}"
            )

    input_index: dict[str, dict[tuple[int, str], dict[str, Any]]] = {
        arm: {} for arm in arm_names
    }
    for (arm, seed), rows in input_rows.items():
        if arm not in input_index:
            continue
        for row in rows:
            key = (int(seed), str(row["query_id"]))
            if key in input_index[arm]:
                raise ValueError(f"Duplicate input task for arm {arm}: {key}")
            input_index[arm][key] = row

    seed_quotas: dict[int, int] = defaultdict(int)
    for seed, _ in selected_keys:
        seed_quotas[seed] += 1
    common_pool_size = len(
        set.intersection(*(set(accepted_index[arm]) for arm in arm_names))
    )
    result: dict[str, Any] = {
        "sampling": {
            "mode": "locked disjoint task-matched retained-query cohort",
            "common_pool_size": common_pool_size,
            "sample_size_per_arm": len(selected_keys),
            "sample_seed": None,
            "seed_quotas": dict(sorted(seed_quotas.items())),
        },
        "checks": {},
        "arms": {},
        "sample_keys": [
            {"sample_index": index, "seed": seed, "query_id": query_id}
            for index, (seed, query_id) in enumerate(selected_keys, start=1)
        ],
    }

    selected_inputs: dict[str, list[dict[str, Any]]] = {}
    for arm in arm_names:
        missing_inputs = [key for key in selected_keys if key not in input_index[arm]]
        if missing_inputs:
            raise ValueError(
                f"Missing {len(missing_inputs)} locked input tasks for arm {arm}; "
                f"first missing key: {missing_inputs[0]}"
            )
        selected_inputs[arm] = [input_index[arm][key] for key in selected_keys]
        selected_accepted = [accepted_index[arm][key] for key in selected_keys]
        result["arms"][arm] = {
            "label": ARM_LABELS.get(arm, arm),
            "metrics": {
                **selection_run_metrics(selected_inputs[arm], history),
                **realized_run_metrics(selected_accepted, history),
            },
        }

    if "C" in selected_inputs and "S" in selected_inputs:
        result["checks"]["c_s_selected_feature_mismatches"] = sum(
            tuple(c_row.get("selected_feature_ids") or [])
            != tuple(s_row.get("selected_feature_ids") or [])
            for c_row, s_row in zip(selected_inputs["C"], selected_inputs["S"])
        )
    return result


def matched_accepted_markdown_report(result: dict[str, Any]) -> str:
    """Render paper-facing metrics without comparing generation acceptance."""

    def percent(value: Any) -> str:
        return "n/a" if value is None else f"{float(value) * 100:.2f}%"

    sampling = result["sampling"]
    lines = [
        "# Matched Retained-Query Historical Composition Metrics",
        "",
        f"Each arm contributes exactly {sampling['sample_size_per_arm']} retained queries ",
        "selected from task IDs satisfying the common filters in all compared arms.",
        f"sampling seed: {sampling['sample_seed']}.",
        "",
        "## Selection provenance",
        "",
        "| Arm | Strict CRR | Selected-ID HUPR | Conditional CRNP |",
        "|---|---:|---:|---:|",
    ]
    for arm in result["arms"]:
        metrics = result["arms"][arm]["metrics"]
        lines.append(
                f"| {result['arms'][arm]['label']} "
                f"| {percent(metrics['input_strict_crr_rate'])} "
                f"| {percent(metrics['selected_unique_hupr'])} "
                f"| {percent(metrics['selected_crnp_rate'])} |"
        )

    lines.extend(
        [
            "",
            "## Realized historical-unseen combinations",
            "",
            "| Arm | Strict CRR | Distinct pairs | Unseen pairs | Unique HUPR | Queries with unseen pair | Core unseen pairs |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for arm in result["arms"]:
        metrics = result["arms"][arm]["metrics"]
        lines.append(
            f"| {result['arms'][arm]['label']} "
            f"| {percent(metrics['input_strict_crr_rate'])} "
            f"| {metrics['unique_mechanism_pairs']} "
            f"| {metrics['unique_unseen_mechanism_pairs']} "
            f"| {percent(metrics['unique_hupr'])} "
            f"| {percent(metrics['query_unseen_composition_rate'])} "
            f"| {metrics['unique_unseen_core_pairs']} |"
        )

    lines.extend(
        [
            "",
            "The comparison conditions on the common structural and quality filters and ",
            "therefore evaluates composition among retained outputs.",
        ]
    )
    return "\n".join(lines) + "\n"


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"Expected object at {path}:{line_number}")
            yield row


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pair(values: Sequence[str]) -> tuple[str, str]:
    if len(values) != 2:
        raise ValueError(f"Expected two values, got {len(values)}")
    left, right = sorted((str(values[0]), str(values[1])))
    return left, right


def build_history_index(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    report_mechanisms: dict[str, set[str]] = defaultdict(set)
    report_feature_ids: dict[str, set[str]] = defaultdict(set)
    feature_provenance: dict[str, set[str]] = defaultdict(set)
    assigned_feature_source: dict[str, str] = {}
    records = 0

    for row in rows:
        source_id = str(row.get("source_id") or "").strip()
        feature_id = str(row.get("feature_id") or "").strip()
        if not source_id or not feature_id:
            raise ValueError("Historical feature rows require source_id and feature_id")
        records += 1
        assigned_feature_source.setdefault(feature_id, source_id)
        feature_provenance[feature_id].add(source_id)
        report_feature_ids[source_id].add(feature_id)
        query = str(
            row.get("query_instance")
            or row.get("query_text")
            or row.get("main_query")
            or ""
        )
        report_mechanisms[source_id].update(detect_semantic_features(query))

    mechanism_pairs: set[tuple[str, str]] = set()
    core_mechanism_pairs: set[tuple[str, str]] = set()
    historical_feature_pairs: set[tuple[str, str]] = set()
    for source_id in report_mechanisms:
        mechanisms = report_mechanisms[source_id]
        mechanism_pairs.update(feature_pairs(mechanisms))
        core_mechanism_pairs.update(
            feature_pairs(semantic_mechanism_features(mechanisms))
        )
        historical_feature_pairs.update(feature_pairs(report_feature_ids[source_id]))

    return {
        "records": records,
        "report_mechanisms": dict(report_mechanisms),
        "report_feature_ids": dict(report_feature_ids),
        "feature_provenance": dict(feature_provenance),
        "assigned_feature_source": assigned_feature_source,
        "mechanism_pairs": frozenset(mechanism_pairs),
        "core_mechanism_pairs": frozenset(core_mechanism_pairs),
        "historical_feature_pairs": frozenset(historical_feature_pairs),
    }


def strict_cross_report_recombination(
    selected_feature_ids: Sequence[str],
    feature_provenance: dict[str, set[str]],
) -> bool:
    """Return true when no one report contains every selected feature ID."""

    selected = list(dict.fromkeys(str(value) for value in selected_feature_ids))
    if len(selected) < 2:
        return False
    provenance_sets: list[set[str]] = []
    for feature_id in selected:
        sources = feature_provenance.get(feature_id)
        if not sources:
            raise ValueError(f"Missing provenance for selected feature {feature_id}")
        provenance_sets.append(set(sources))
    return not set.intersection(*provenance_sets)


def selection_run_metrics(
    rows: Sequence[dict[str, Any]], history: dict[str, Any]
) -> dict[str, Any]:
    provenance = history["feature_provenance"]
    assigned = history["assigned_feature_source"]
    historical_pairs = history["historical_feature_pairs"]
    strict_crr_count = 0
    assigned_crr_count = 0
    pair_occurrences = 0
    unseen_occurrences = 0
    cross_report_occurrences = 0
    cross_report_novel_occurrences = 0
    unique_pairs: set[tuple[str, str]] = set()

    for row in rows:
        selected = list(
            dict.fromkeys(str(value) for value in row.get("selected_feature_ids") or [])
        )
        if len(selected) < 2:
            raise ValueError("Every ablation input must select at least two features")
        missing = [feature_id for feature_id in selected if feature_id not in assigned]
        if missing:
            raise ValueError(f"Selected features missing from history: {missing}")
        if strict_cross_report_recombination(selected, provenance):
            strict_crr_count += 1
        if len({assigned[feature_id] for feature_id in selected}) >= 2:
            assigned_crr_count += 1
        selected_pairs = set(feature_pairs(selected))
        unique_pairs.update(selected_pairs)
        pair_occurrences += len(selected_pairs)
        for pair in selected_pairs:
            unseen = pair not in historical_pairs
            cross_report = assigned[pair[0]] != assigned[pair[1]]
            unseen_occurrences += int(unseen)
            cross_report_occurrences += int(cross_report)
            cross_report_novel_occurrences += int(unseen and cross_report)

    unique_unseen_pairs = unique_pairs - historical_pairs
    count = len(rows)
    return {
        "input_tasks": count,
        "input_assigned_crr_rate": assigned_crr_count / count if count else None,
        "input_strict_crr_rate": strict_crr_count / count if count else None,
        "selected_unique_pairs": len(unique_pairs),
        "selected_unique_unseen_pairs": len(unique_unseen_pairs),
        "selected_unique_hupr": len(unique_unseen_pairs) / len(unique_pairs)
        if unique_pairs
        else None,
        "selected_occurrence_hupr": unseen_occurrences / pair_occurrences
        if pair_occurrences
        else None,
        "selected_cross_report_pair_rate": cross_report_occurrences / pair_occurrences
        if pair_occurrences
        else None,
        "selected_crnp_rate": cross_report_novel_occurrences
        / cross_report_occurrences
        if cross_report_occurrences
        else None,
    }


def realized_run_metrics(
    rows: Sequence[dict[str, Any]], history: dict[str, Any]
) -> dict[str, Any]:
    historical_pairs = history["mechanism_pairs"]
    historical_core_pairs = history["core_mechanism_pairs"]
    unique_pairs: set[tuple[str, str]] = set()
    unique_core_pairs: set[tuple[str, str]] = set()
    pair_occurrences = 0
    unseen_occurrences = 0
    core_pair_occurrences = 0
    core_unseen_occurrences = 0
    queries_with_unseen = 0
    queries_with_core_unseen = 0

    for row in rows:
        query = str(row.get("main_query") or row.get("query") or "")
        mechanisms = detect_semantic_features(query)
        pairs = set(feature_pairs(mechanisms))
        core_pairs = set(feature_pairs(semantic_mechanism_features(mechanisms)))
        unseen = pairs - historical_pairs
        core_unseen = core_pairs - historical_core_pairs
        unique_pairs.update(pairs)
        unique_core_pairs.update(core_pairs)
        pair_occurrences += len(pairs)
        unseen_occurrences += len(unseen)
        core_pair_occurrences += len(core_pairs)
        core_unseen_occurrences += len(core_unseen)
        queries_with_unseen += int(bool(unseen))
        queries_with_core_unseen += int(bool(core_unseen))

    unique_unseen = unique_pairs - historical_pairs
    unique_core_unseen = unique_core_pairs - historical_core_pairs
    count = len(rows)
    return {
        "accepted_queries": count,
        "unique_mechanism_pairs": len(unique_pairs),
        "unique_unseen_mechanism_pairs": len(unique_unseen),
        "unique_hupr": len(unique_unseen) / len(unique_pairs) if unique_pairs else None,
        "occurrence_hupr": unseen_occurrences / pair_occurrences
        if pair_occurrences
        else None,
        "query_unseen_composition_rate": queries_with_unseen / count if count else None,
        "unique_core_mechanism_pairs": len(unique_core_pairs),
        "unique_unseen_core_pairs": len(unique_core_unseen),
        "unique_core_hupr": len(unique_core_unseen) / len(unique_core_pairs)
        if unique_core_pairs
        else None,
        "core_occurrence_hupr": core_unseen_occurrences / core_pair_occurrences
        if core_pair_occurrences
        else None,
        "core_query_unseen_composition_rate": queries_with_core_unseen / count
        if count
        else None,
        "pair_occurrences": pair_occurrences,
        "unseen_pair_occurrences": unseen_occurrences,
        "core_pair_occurrences": core_pair_occurrences,
        "core_unseen_pair_occurrences": core_unseen_occurrences,
    }


def aggregate_run_metrics(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    excluded = {"arm", "run", "seed"}
    metric_names = [
        key
        for key, value in rows[0].items()
        if key not in excluded and isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    result: dict[str, Any] = {}
    for name in metric_names:
        values = [float(row[name]) for row in rows if row.get(name) is not None]
        result[name] = {
            "mean": statistics.fmean(values) if values else None,
            "sd": statistics.stdev(values) if len(values) >= 2 else 0.0,
        }
    return result


def _format_metric(metric: dict[str, Any], *, percent: bool = False) -> str:
    mean = float(metric["mean"])
    sd = float(metric["sd"])
    if percent:
        return f"{mean * 100:.2f}% +/- {sd * 100:.2f} pp"
    return f"{mean:.1f} +/- {sd:.1f}"


def markdown_report(result: dict[str, Any]) -> str:
    lines = [
        "# Historical Cross-Report Composition Metrics",
        "",
        "The historical co-occurrence unit is a complete report (`source_id`). ",
        "Values are arithmetic mean +/- sample standard deviation over three ",
        "real matched 1,000-task generation runs.",
        "",
        "## Historical index",
        "",
        f"- Reports: {result['history']['reports']}",
        f"- Historical mechanism pairs: {result['history']['mechanism_pairs']}",
        f"- Historical core-mechanism pairs: {result['history']['core_mechanism_pairs']}",
        f"- Historical feature-ID pairs: {result['history']['feature_id_pairs']}",
        "",
        "## Input provenance audit",
        "",
        "| Arm | Runs | Input tasks | Strict CRR | Selected-ID HUPR | Conditional CRNP |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for arm in ("R", "C", "S"):
        item = result["arms"][arm]
        agg = item["aggregate"]
        lines.append(
            "| "
            + item["label"]
            + " | "
            + " | ".join(
                [
                    str(item["run_count"]),
                    _format_metric(agg["input_tasks"]),
                    _format_metric(agg["input_strict_crr_rate"], percent=True),
                    _format_metric(agg["selected_unique_hupr"], percent=True),
                    _format_metric(agg["selected_crnp_rate"], percent=True),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "These feature-ID metrics are selection-side sanity checks. Their near-saturation ",
            "makes them unsuitable as comparative effectiveness endpoints.",
            "",
            "## Realized historical-unseen combinations",
            "",
            "| Arm | Accepted | Distinct pairs | Unseen pairs | Unique HUPR | Occurrence HUPR | Queries with unseen pair | Core unseen pairs |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for arm in ("R", "C", "S"):
        item = result["arms"][arm]
        agg = item["aggregate"]
        lines.append(
            "| "
            + item["label"]
            + " | "
            + " | ".join(
                [
                    _format_metric(agg["accepted_queries"]),
                    _format_metric(agg["unique_mechanism_pairs"]),
                    _format_metric(agg["unique_unseen_mechanism_pairs"]),
                    _format_metric(agg["unique_hupr"], percent=True),
                    _format_metric(agg["occurrence_hupr"], percent=True),
                    _format_metric(
                        agg["query_unseen_composition_rate"], percent=True
                    ),
                    _format_metric(agg["unique_unseen_core_pairs"]),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Definitions",
            "",
            "- Strict CRR: fraction of input tasks for which no single historical report contains all selected feature IDs.",
            "- Selected-ID HUPR: fraction of distinct selected feature-ID pairs absent from every historical report.",
            "- Conditional CRNP: historically unseen selected feature-ID pair occurrences divided by cross-report selected pair occurrences.",
            "- Unique HUPR: fraction of distinct mechanism pairs in accepted main queries absent from every historical report.",
            "- Occurrence HUPR: the corresponding occurrence-weighted fraction over accepted main queries.",
            "- Queries with unseen pair: fraction of accepted main queries containing at least one historically unseen mechanism pair.",
            "",
            "The realized metrics establish historical novelty at the mechanism-pair level. They do not attribute each realized mechanism to a particular selected feature or source report.",
        ]
    )
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze cross-report selection and historical-unseen composition."
    )
    parser.add_argument("--features", required=True)
    parser.add_argument("--accepted", required=True)
    parser.add_argument("--inputs-root", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--expected-runs", type=int, default=None)
    parser.add_argument("--expected-inputs", type=int, default=None)
    parser.add_argument("--matched-accepted-sample-size", type=int, default=None)
    parser.add_argument("--sample-seed", type=int, default=20260921)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    features_path = Path(args.features)
    accepted_path = Path(args.accepted)
    inputs_root = Path(args.inputs_root)
    output_dir = Path(args.output_dir)

    history = build_history_index(iter_jsonl(features_path))
    accepted_rows = list(iter_jsonl(accepted_path))

    if args.matched_accepted_sample_size is not None:
        seeds = sorted({int(row["seed"]) for row in accepted_rows})
        input_rows: dict[tuple[str, int], list[dict[str, Any]]] = {}
        for arm, filename in ARM_FILES.items():
            for seed in seeds:
                input_path = inputs_root / f"seed_{seed}" / filename
                rows = list(iter_jsonl(input_path))
                if args.expected_inputs is not None and len(rows) != args.expected_inputs:
                    raise ValueError(
                        f"Expected {args.expected_inputs} rows in {input_path}, "
                        f"got {len(rows)}"
                    )
                input_rows[(arm, seed)] = rows

        result = analyze_matched_accepted_sample(
            accepted_rows,
            input_rows,
            history,
            sample_size=args.matched_accepted_sample_size,
            sample_seed=args.sample_seed,
        )
        result["definitions"] = {
            "historical_unit": "source_id (complete report)",
            "generated_scope": "task-matched accepted Stage3 main_query",
            "strict_crr": "no single report contains all selected feature IDs",
            "selected_crnp": "historically unseen selected feature-ID pair occurrences divided by assigned cross-report pair occurrences",
            "hupr": "historically unseen detected mechanism pairs divided by detected mechanism pairs",
        }
        result["sources"] = {
            "features": str(features_path),
            "features_sha256": file_sha256(features_path),
            "accepted": str(accepted_path),
            "accepted_sha256": file_sha256(accepted_path),
            "inputs_root": str(inputs_root),
        }
        result["history"] = {
            "feature_records": history["records"],
            "unique_feature_ids": len(history["feature_provenance"]),
            "reports": len(history["report_mechanisms"]),
            "mechanism_pairs": len(history["mechanism_pairs"]),
            "core_mechanism_pairs": len(history["core_mechanism_pairs"]),
            "feature_id_pairs": len(history["historical_feature_pairs"]),
        }

        output_dir.mkdir(parents=True, exist_ok=True)
        summary_json = output_dir / "summary.json"
        summary_md = output_dir / "summary.md"
        sample_jsonl = output_dir / "matched_sample.jsonl"
        summary_json.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        summary_md.write_text(
            matched_accepted_markdown_report(result), encoding="utf-8"
        )
        with sample_jsonl.open("w", encoding="utf-8", newline="\n") as handle:
            for row in result["sample_keys"]:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        print(summary_json)
        print(summary_md)
        print(sample_jsonl)
        return

    accepted_groups: dict[tuple[str, int, int], list[dict[str, Any]]] = defaultdict(list)
    for row in accepted_rows:
        key = (str(row["arm"]), int(row["run"]), int(row["seed"]))
        accepted_groups[key].append(row)

    by_arm: dict[str, list[dict[str, Any]]] = defaultdict(list)
    input_by_arm_seed: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for (arm, run, seed), cases in sorted(accepted_groups.items()):
        if arm not in ARM_FILES:
            raise ValueError(f"Unknown ablation arm: {arm}")
        input_path = inputs_root / f"seed_{seed}" / ARM_FILES[arm]
        inputs = list(iter_jsonl(input_path))
        if args.expected_inputs is not None and len(inputs) != args.expected_inputs:
            raise ValueError(
                f"Expected {args.expected_inputs} rows in {input_path}, got {len(inputs)}"
            )
        input_by_arm_seed[(arm, seed)] = inputs
        row = {
            "arm": arm,
            "run": run,
            "seed": seed,
            **selection_run_metrics(inputs, history),
            **realized_run_metrics(cases, history),
        }
        by_arm[arm].append(row)

    for arm in ARM_FILES:
        if args.expected_runs is not None and len(by_arm[arm]) != args.expected_runs:
            raise ValueError(
                f"Expected {args.expected_runs} runs for {arm}, got {len(by_arm[arm])}"
            )

    cs_mismatches = 0
    shared_cs_tasks = 0
    for seed in sorted({seed for _, _, seed in accepted_groups}):
        c_rows = {
            str(row["query_id"]): tuple(row.get("selected_feature_ids") or [])
            for row in input_by_arm_seed[("C", seed)]
        }
        s_rows = {
            str(row["query_id"]): tuple(row.get("selected_feature_ids") or [])
            for row in input_by_arm_seed[("S", seed)]
        }
        shared = set(c_rows) & set(s_rows)
        shared_cs_tasks += len(shared)
        cs_mismatches += sum(c_rows[key] != s_rows[key] for key in shared)

    result: dict[str, Any] = {
        "definitions": {
            "historical_unit": "source_id (complete report)",
            "generated_scope": "accepted Stage3 main_query",
            "strict_crr": "no single report contains all selected feature IDs",
            "selected_crnp": "historically unseen selected feature-ID pair occurrences divided by assigned cross-report pair occurrences",
            "hupr": "historically unseen detected mechanism pairs divided by detected mechanism pairs",
        },
        "sources": {
            "features": str(features_path),
            "features_sha256": file_sha256(features_path),
            "accepted": str(accepted_path),
            "accepted_sha256": file_sha256(accepted_path),
            "inputs_root": str(inputs_root),
        },
        "history": {
            "feature_records": history["records"],
            "unique_feature_ids": len(history["feature_provenance"]),
            "reports": len(history["report_mechanisms"]),
            "mechanism_pairs": len(history["mechanism_pairs"]),
            "core_mechanism_pairs": len(history["core_mechanism_pairs"]),
            "feature_id_pairs": len(history["historical_feature_pairs"]),
        },
        "checks": {
            "shared_c_s_input_tasks": shared_cs_tasks,
            "c_s_selected_feature_mismatches": cs_mismatches,
        },
        "arms": {},
    }
    for arm in ("R", "C", "S"):
        rows = sorted(by_arm[arm], key=lambda row: row["run"])
        result["arms"][arm] = {
            "label": ARM_LABELS[arm],
            "run_count": len(rows),
            "per_run": rows,
            "aggregate": aggregate_run_metrics(rows),
        }

    output_dir.mkdir(parents=True, exist_ok=True)
    summary_json = output_dir / "summary.json"
    summary_md = output_dir / "summary.md"
    per_run_jsonl = output_dir / "per_run.jsonl"
    summary_json.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    summary_md.write_text(markdown_report(result), encoding="utf-8")
    with per_run_jsonl.open("w", encoding="utf-8", newline="\n") as handle:
        for arm in ("R", "C", "S"):
            for row in result["arms"][arm]["per_run"]:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    print(summary_json)
    print(summary_md)
    print(per_run_jsonl)


if __name__ == "__main__":
    main()
