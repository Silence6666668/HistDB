"""Bounded, generation-only 4-complexity x W/R/C/S development pilot.

Reuses frozen Stage2 inputs and the unmodified Stage3 generator. The budget is
input tasks, not guaranteed accepted queries. Never tops up rejected tasks.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.metadata
import json
import os
import random
import statistics
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.split_jsonl_into_shards import stable_shard_index
from src.common.config import load_yaml
from src.llm.model_client import ModelConfig, resolve_model_config

PROFILES = ("default", "large_neo4j", "planner_stress_neo4j", "engine_stress_neo4j")
LANES = {"W": "within_report_skeleton", "R": "random_feature_only", "C": "conditioned_feature_only", "S": "skeleton_feature"}
DEFAULT_SOURCE = REPO_ROOT / "data/ablation_runs/neo4j_repeated_skeleton_guidance_5x1000_20260830"


def read_rows(path: Path, *, live: bool = False) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip() and (not live or line.endswith("\n"))]


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_pilot_inputs(source_root: Path, seeds: list[int], sample_size: int, sample_seed: int) -> tuple[dict[str, list[dict]], list[dict]]:
    if sample_size <= 0 or len(seeds) != len(set(seeds)):
        raise ValueError("positive sample size and unique seeds required")
    indexed: dict[str, dict[tuple[int, str], dict]] = {arm: {} for arm in LANES}
    sources = []
    for seed in seeds:
        for arm, lane in LANES.items():
            sub = "inputs_within_report_strict" if arm == "W" else "inputs"
            path = source_root / sub / f"seed_{seed}" / f"{lane}.jsonl"
            manifest_path = path.parent / "input_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
            entry = manifest["lane"] if arm == "W" else manifest["lanes"][lane]
            digest = file_hash(path)
            if digest != entry["sha256"]:
                raise ValueError(f"input source hash mismatch: {path}")
            rows = read_rows(path)
            if len(rows) != entry["count"]:
                raise ValueError(f"input source count mismatch: {path}")
            for row in rows:
                query_id = str(row.get("query_id") or "").strip()
                key = (seed, query_id)
                if not query_id or key in indexed[arm]:
                    raise ValueError(f"empty or duplicate query ID: {arm}/{key}")
                indexed[arm][key] = row
            sources.append({"arm": arm, "seed": seed, "path": str(path.resolve()), "count": len(rows), "sha256": digest, "input_manifest": str(manifest_path.resolve()), "input_manifest_sha256": file_hash(manifest_path)})

    common = set.intersection(*(set(rows) for rows in indexed.values()))
    # Validate the whole eligible pool before outcome-independent sampling.
    for key in sorted(common):
        w, r, c, s = (indexed[arm][key] for arm in ("W", "R", "C", "S"))
        for arm, row in (("W", w), ("R", r), ("C", c), ("S", s)):
            ids = row.get("selected_feature_ids") or []
            if len(ids) != 3 or len(set(ids)) != 3:
                raise ValueError(f"three distinct features required: {arm}/{key}")
        if len({str(row.get("source_id")) for row in (w, r, c, s)}) != 1:
            raise ValueError(f"source report mismatch: {key}")
        for field in ("selected_feature_ids", "feature_items", "retrieval_query", "fragment_groups"):
            if c.get(field) != s.get(field):
                raise ValueError(f"C/S mismatch: {key}/{field}")
        for arm, row in (("R", r), ("C", c)):
            if any(row.get(field) not in (None, "", []) for field in ("skeleton_text", "guidance_skeleton_text", "holes", "selected_skeleton_id")):
                raise ValueError(f"feature-only skeleton leak: {arm}/{key}")
        if not s.get("guidance_skeleton_text") or w.get("guidance_skeleton_text") != s.get("guidance_skeleton_text"):
            raise ValueError(f"W/S skeleton mismatch: {key}")
        source = str(w.get("source_id") or "")
        if w.get("same_report_source_id") != source or w.get("selected_feature_source_ids") != [source]:
            raise ValueError(f"within-report feature provenance mismatch: {key}")
    if len(common) < sample_size:
        raise ValueError(f"need {sample_size} matched input tasks; found {len(common)}")
    selected = sorted(random.Random(sample_seed).sample(sorted(common), sample_size))
    result: dict[str, list[dict]] = {arm: [] for arm in LANES}
    for arm in LANES:
        for seed, original_id in selected:
            row = copy.deepcopy(indexed[arm][(seed, original_id)])
            row.update(query_id=f"seed_{seed}:{original_id}", pilot_original_query_id=original_id, pilot_generation_seed=seed, pilot_arm=arm)
            result[arm].append(row)
    return result, sources


def build_jobs(output_root: Path, shards: int) -> list[dict]:
    if shards <= 0:
        raise ValueError("positive shard count required")
    return [
        {"profile": profile, "arm": arm, "shard": shard,
         "input": str(output_root / "inputs/shards" / arm / f"shard_{shard:02d}.jsonl"),
         "output": str(output_root / "cells" / profile / arm / "stage3_shards" / f"shard_{shard:02d}")}
        for shard in range(1, shards + 1) for profile in PROFILES for arm in LANES
    ]


def stage3_command(job: dict, output_root: Path, *, preflight: bool) -> list[str]:
    cmd = [sys.executable, "-m", "src.cli.run_stage3", "--input", job["input"], "--output-dir", job["output"],
           "--model-config", str(output_root / "frozen/model.json"), "--pipeline-config", str(output_root / "frozen/pipeline.json"),
           "--prompt", str(output_root / "frozen/instantiate_case_system.txt"), "--mode", "llm", "--variants-per-input", "1",
           "--family-bias", "none", "--complexity-profile", job["profile"], "--generation-profile", "portable_crossdb",
           "--diversity-profile", "semantic_edges", "--quality-retry-limit", "2", "--resume"]
    if preflight:
        cmd += ["--max-items", "1"]
    return cmd


def prepare_run(source_root: Path, output_root: Path, seeds: list[int], sample_size: int, sample_seed: int, shards: int) -> dict:
    if output_root.exists():
        raise FileExistsError(f"refusing to overwrite existing pilot: {output_root}")
    if shards <= 0 or shards > sample_size:
        raise ValueError("shards must be in 1..sample_size")
    rows_by_arm, sources = build_pilot_inputs(source_root, seeds, sample_size, sample_seed)
    model = resolve_model_config(load_yaml(REPO_ROOT / "configs/model.yaml"), task="instantiation")
    ModelConfig(**model)  # Reject unsupported fields, including literal credentials.
    output_root.mkdir(parents=True)
    frozen_files = []

    def record_file(path: Path) -> None:
        frozen_files.append({"path": str(path.resolve()), "sha256": file_hash(path)})

    for arm, rows in rows_by_arm.items():
        path = output_root / "inputs" / f"{arm}.jsonl"
        write_rows(path, rows)
        record_file(path)
        groups = [[] for _ in range(shards)]
        for index, row in enumerate(rows):
            groups[stable_shard_index(row, index, shards, "query_id")].append(row)
        if not all(groups):
            raise ValueError("empty shard; reduce shard count")
        for index, group in enumerate(groups, 1):
            path = output_root / "inputs/shards" / arm / f"shard_{index:02d}.jsonl"
            write_rows(path, group)
            record_file(path)
    write_json(output_root / "frozen/model.json", model)
    write_json(output_root / "frozen/pipeline.json", load_yaml(REPO_ROOT / "configs/pipeline.yaml"))
    prompt = output_root / "frozen/instantiate_case_system.txt"
    prompt.write_bytes((REPO_ROOT / "prompts/instantiate_case_system.txt").read_bytes())
    for path in sorted((output_root / "frozen").iterdir()):
        record_file(path)
    # Archive executable source; fail closed if live code changes before a job.
    for path in sorted((REPO_ROOT / "src").rglob("*.py")) + [Path(__file__).resolve()]:
        snapshot = output_root / "code_snapshot" / path.relative_to(REPO_ROOT)
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes(path.read_bytes())
        record_file(path)
        record_file(snapshot)
    for source in sources:
        record_file(Path(source["path"]))
        record_file(Path(source["input_manifest"]))
    feature_path = REPO_ROOT / "data/stage1/features.jsonl"
    record_file(feature_path)
    versions = {}
    for name in ("openai", "PyYAML"):
        versions[name] = importlib.metadata.version(name)
    manifest = {
        "created_utc": utc_now(), "purpose": "development pilot, not formal RQ2 30-cohort evidence",
        "profiles": list(PROFILES), "arms": list(LANES), "tasks_per_cell": sample_size,
        "total_generation_tasks": sample_size * 16, "generation_seeds": seeds, "sampling_seed": sample_seed,
        "source_report_count": len({row.get("source_id") for row in rows_by_arm["W"]}),
        "sampling_population": "tasks eligible for strict within-report three-feature selection; all four arms use identical task keys",
        "database_execution": False, "generation_profile": "portable_crossdb", "diversity_profile": "semantic_edges",
        "quality_retry_limit": 2, "variants_per_input": 1, "family_bias": "none", "shards_per_cell": shards,
        "budget_unit": "input tasks; retained queries may be fewer; no top-ups of rejected/error tasks",
        "retry_note": "unchanged Stage3 includes internal validation/dialect retries and outer quality retries; model transport retries are separate",
        "engine_stress_note": "engine_stress_neo4j is a write-focused prompt, not a monotone complexity level; portable_crossdb still forbids schema DDL, so some prompt preferences conflict; report separately",
        "python": sys.version, "package_versions": versions, "source_inputs": sources,
        "frozen_files": frozen_files, "jobs": build_jobs(output_root, shards),
    }
    write_json(output_root / "manifest.json", manifest)
    write_json(output_root / "state.json", {"status": "prepared", "updated_utc": utc_now(), "total_generation_tasks": sample_size * 16})
    return manifest


def verify_frozen_files(output_root: Path) -> dict:
    manifest = json.loads((output_root / "manifest.json").read_text(encoding="utf-8"))
    for entry in manifest["frozen_files"]:
        path = Path(entry["path"])
        if not path.is_file() or file_hash(path) != entry["sha256"]:
            raise ValueError(f"frozen file hash mismatch: {path}")
    return manifest


def collect_status(output_root: Path) -> dict:
    manifest = json.loads((output_root / "manifest.json").read_text(encoding="utf-8"))
    cells = []
    selected_cells = dict.fromkeys((job["profile"], job["arm"]) for job in manifest["jobs"])
    for profile, arm in selected_cells:
        shards = [Path(job["output"]) for job in manifest["jobs"] if job["profile"] == profile and job["arm"] == arm]
        counts = {"completed": 0, "accepted": 0, "rejected": 0, "errors": 0}
        for shard in shards:
            for name, filename in (("completed", "completed_variants.jsonl"), ("accepted", "instantiated_cases.jsonl"), ("rejected", "rejected_cases.jsonl"), ("errors", "errors.jsonl")):
                counts[name] += len(read_rows(shard / filename, live=True))
        cells.append({"profile": profile, "arm": arm, "input_tasks": manifest["tasks_per_cell"], **counts})
    return {"observed_utc": utc_now(), "total_generation_tasks": manifest["total_generation_tasks"], "completed_tasks": sum(cell["completed"] for cell in cells), "cells": cells}


def pending_preflight_jobs(jobs: list[dict]) -> list[dict]:
    """Do not reclassify completed generation errors as preflight failures on resume."""
    return [job for job in jobs if job["shard"] == 1 and not read_rows(Path(job["output"]) / "completed_variants.jsonl")]


def execute_jobs(output_root: Path, jobs: list[dict], workers: int, *, preflight: bool) -> None:
    cancelled = threading.Event()

    def execute(job: dict) -> dict:
        if cancelled.is_set() or (output_root / "STOP").exists():
            raise RuntimeError("pilot stopped before launching another shard")
        verify_frozen_files(output_root)
        destination = Path(job["output"])
        destination.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        log = destination / ("preflight.log" if preflight else "run.log")
        with log.open("a", encoding="utf-8") as handle:
            result = subprocess.run(stage3_command(job, output_root, preflight=preflight), cwd=REPO_ROOT, env=env, stdout=handle, stderr=subprocess.STDOUT, timeout=14400)
        if result.returncode:
            cancelled.set()
            raise RuntimeError(f"Stage3 exited {result.returncode}: {log}")
        summary = json.loads((destination / "summary.json").read_text(encoding="utf-8"))
        if (preflight and summary["errors"]) or (not preflight and summary["errors"] >= 10):
            cancelled.set()
            raise RuntimeError(f"operational errors; inspect {destination / 'errors.jsonl'}")
        return summary

    failures = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(execute, job): job for job in jobs}
        for future in as_completed(futures):
            job = futures[future]
            try:
                summary = future.result()
                print(json.dumps({"phase": "preflight" if preflight else "generation", "profile": job["profile"], "arm": job["arm"], "shard": job["shard"], "processed": summary["processed"], "accepted": summary["valid_cases"], "errors": summary["errors"]}), flush=True)
            except Exception as exc:
                cancelled.set()
                failures.append(str(exc))
            write_json(output_root / "progress.json", collect_status(output_root))
    if failures:
        raise RuntimeError("; ".join(failures))


def finalize(output_root: Path) -> dict:
    from scripts.analyze_tool_query_sample_metrics import query_metrics, summarize
    manifest = verify_frozen_files(output_root)
    result = collect_status(output_root)
    inputs = {arm: read_rows(output_root / "inputs" / f"{arm}.jsonl") for arm in LANES}
    for cell in result["cells"]:
        expected = {row["query_id"] for row in inputs[cell["arm"]]}
        grouped = {"accepted": [], "rejected": [], "errors": []}
        completed = []
        for job in manifest["jobs"]:
            if (job["profile"], job["arm"]) != (cell["profile"], cell["arm"]):
                continue
            shard = Path(job["output"])
            completed.extend(read_rows(shard / "completed_variants.jsonl"))
            for name, filename in (("accepted", "instantiated_cases.jsonl"), ("rejected", "rejected_cases.jsonl"), ("errors", "errors.jsonl")):
                grouped[name].extend(read_rows(shard / filename))
        disposition_ids = [row["query_id"] for rows in grouped.values() for row in rows]
        if len(disposition_ids) != len(expected) or set(disposition_ids) != expected or len(completed) != len(expected) or {row["query_id"] for row in completed} != expected:
            raise ValueError(f"incomplete or duplicate task dispositions: {cell['profile']}/{cell['arm']}")
        main = output_root / "cells" / cell["profile"] / cell["arm"] / "stage3_main"
        for name, filename in (("accepted", "instantiated_cases.jsonl"), ("rejected", "rejected_cases.jsonl"), ("errors", "errors.jsonl")):
            write_rows(main / filename, grouped[name])
        metrics = [query_metrics({**row, "query": row["main_query"]}) for row in grouped["accepted"]]
        cell["accepted_rate"] = cell["accepted"] / cell["input_tasks"]
        cell["metrics"] = summarize(metrics) if metrics else None
        cell["mean_setup_statements"] = statistics.fmean(len(row.get("setup_queries") or []) for row in grouped["accepted"]) if metrics else None
        write_json(main / "summary.json", cell)
    result.update(purpose=manifest["purpose"], database_execution=False, source_report_count=manifest["source_report_count"], engine_stress_note=manifest["engine_stress_note"])
    write_json(output_root / "summary.json", result)
    lines = ["# RQ2 complexity pilot", "", "Generation-only development pilot. All arms use the same 1,000 (or configured sample-size) strict-W-eligible task instances. No database execution or accepted-query top-up.", "", "| Complexity | Arm | Completed | Accepted | Rejected | Errors | Mean clause-keyword count |", "|---|---|---:|---:|---:|---:|---:|"]
    for cell in result["cells"]:
        clauses = cell["metrics"]["mean_clause_count"] if cell["metrics"] else 0
        lines.append(f"| {cell['profile']} | {cell['arm']} | {cell['completed']} | {cell['accepted']} | {cell['rejected']} | {cell['errors']} | {clauses:.2f} |")
    lines += ["", "Clause counts are the existing regex-based diagnostic, not parsed AST counts. Raw diversity counts have unequal accepted-query denominators and are not equal-size comparisons.", "", manifest["engine_stress_note"], "", "This is not the formal RQ2 thirty-cohort corpus and is not thirty independent runs."]
    (output_root / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--seeds", default="20260842,20260843,20260844")
    parser.add_argument("--sample-size", type=int, default=1000)
    parser.add_argument("--sample-seed", type=int, default=20261012)
    parser.add_argument("--shards", type=int, default=4)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--status-only", action="store_true")
    args = parser.parse_args()
    output_root = args.output_root.resolve()
    if args.status_only:
        print(json.dumps(collect_status(output_root), indent=2))
        return 0
    if not 1 <= args.workers <= 8:
        raise ValueError("workers must be 1..8 to avoid the previous high-concurrency rate-limit failure")
    manifest = verify_frozen_files(output_root) if args.resume else prepare_run(args.source_root.resolve(), output_root, [int(seed) for seed in args.seeds.split(",")], args.sample_size, args.sample_seed, args.shards)
    if args.prepare_only:
        print(json.dumps({"status": "prepared", "output_root": str(output_root), "total_generation_tasks": manifest["total_generation_tasks"]}))
        return 0
    cfg = load_yaml(output_root / "frozen/model.json")
    if cfg.get("provider") != "openai_compatible" or not os.getenv(cfg.get("api_key_env", "DEEPSEEK_API_KEY")):
        raise RuntimeError("real configured model and API key are required; no mock substitution")
    try:
        write_json(output_root / "state.json", {"status": "preflight", "pid": os.getpid(), "updated_utc": utc_now()})
        execute_jobs(output_root, pending_preflight_jobs(manifest["jobs"]), args.workers, preflight=True)
        write_json(output_root / "state.json", {"status": "running", "pid": os.getpid(), "updated_utc": utc_now(), "total_generation_tasks": manifest["total_generation_tasks"]})
        execute_jobs(output_root, manifest["jobs"], args.workers, preflight=False)
        result = finalize(output_root)
        write_json(output_root / "state.json", {"status": "complete", "pid": os.getpid(), "updated_utc": utc_now(), "completed_tasks": result["completed_tasks"]})
        print(json.dumps({"status": "complete", "completed_tasks": result["completed_tasks"]}), flush=True)
    except Exception as exc:
        write_json(output_root / "state.json", {"status": "failed", "pid": os.getpid(), "updated_utc": utc_now(), "error": str(exc)})
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
