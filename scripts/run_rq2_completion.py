"""Top up four generation-only RQ2 groups without overwriting legacy runs.

Each group targets 30,000 statically retained queries, not 30 independent runs.
Pair keys are retained as diagnostics; independently filled groups must not be
represented as thirty task-matched cohorts. S reuses only a fixed 4,000-row
legacy sample. All completed rejections/errors remain final dispositions.
"""
from __future__ import annotations

import argparse
import copy
import importlib.metadata
import json
import os
import random
import shlex
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.run_rq2_complexity_pilot import (
    DEFAULT_SOURCE, file_hash, read_rows, stage3_command, utc_now,
    write_json, write_rows,
)
from scripts.rq2_current_inputs import InputBuilder
from scripts.rq2_existing_sources import load_registered_sources
from src.common.config import load_yaml
from src.llm.model_client import ModelConfig, resolve_model_config

CELLS = tuple(f"default/{arm}" for arm in "WRCS")
DISPOSITIONS = ("instantiated_cases.jsonl", "rejected_cases.jsonl", "errors.jsonl")
DEFAULT_PILOT = REPO_ROOT / "data/ablation_runs/rq2_complexity_pilot_4x4_1000_20261012"
DEFAULT_OUTPUT = REPO_ROOT / "data/ablation_runs/rq2_completion_30k_20261013"


def validate_workers(workers: int) -> None:
    if not 1 <= workers <= 48:
        raise ValueError("workers must be 1..48")


def verify_files(entries: list[dict]) -> None:
    for entry in entries:
        path = Path(entry["path"])
        if not path.is_file() or file_hash(path) != entry["sha256"]:
            raise ValueError(f"hash mismatch: {path}")


def build_jobs(batch_root: Path, inputs: dict[str, list[dict]],
               active_cells: list[str], shards: int) -> list[dict]:
    if shards < 1:
        raise ValueError("shards must be positive")
    jobs = []
    for cell in active_cells:
        if cell not in CELLS:
            raise ValueError(f"out-of-scope cell: {cell}")
        profile, arm = cell.split("/")
        rows = inputs[arm]
        for shard in range(min(shards, len(rows))):
            group = rows[shard::shards]
            # When there are fewer rows than shards, each row still has one job.
            path = batch_root / "inputs/shards" / arm / f"shard_{shard+1:02d}.jsonl"
            if path.exists():
                raise FileExistsError(f"refusing to overwrite batch input: {path}")
            write_rows(path, group)
            jobs.append({"profile": profile, "arm": arm, "shard": shard+1,
                         "input": str(path.resolve()), "count": len(group),
                         "output": str((batch_root / "cells" / profile / arm /
                                        "stage3_shards" / f"shard_{shard+1:02d}").resolve())})
    return jobs


def limit_legacy_reuse(accepted: dict, *, s_keep: int, seed: int) -> dict:
    result = {cell: dict(accepted.get(cell, {})) for cell in CELLS}
    if not 0 <= s_keep <= len(result["default/S"]):
        raise ValueError(f"insufficient S legacy rows for requested reuse: {s_keep}")
    ids = sorted(result["default/S"])
    chosen = sorted(random.Random(seed).sample(ids, s_keep))
    result["default/S"] = {key: result["default/S"][key] for key in chosen}
    return result


def pair_maps(accepted: dict, panel: str) -> dict[str, dict[str, dict]]:
    maps = {}
    for arm in panel:
        mapped = {}
        for qid, row in accepted[f"default/{arm}"].items():
            keys = row.get("rq2_pair_keys")
            key = keys.get(panel) if keys is not None else qid
            if key is None:
                continue
            if key in mapped:
                raise ValueError(f"duplicate panel task key: {panel}/{arm}/{key}")
            mapped[key] = row
        maps[arm] = mapped
    return maps


def status_from_accepted(accepted: dict, target: int) -> dict:
    if target < 1:
        raise ValueError("positive retained target required")
    counts = {cell: len(accepted.get(cell, {})) for cell in CELLS}
    common = set.intersection(*(set(accepted[cell]) for cell in CELLS))
    active = [cell for cell in CELLS if counts[cell] < target]
    result = {"observed_utc": utc_now(), "retained": counts, "target_per_group": target,
              "matched_default": len(common), "active_cells": active,
              "complete": not active, "database_execution": False}
    for panel in ("WS", "RCS"):
        maps = pair_maps(accepted, panel)
        result[f"matched_{panel}"] = len(set.intersection(*(set(maps[a]) for a in panel)))
    return result


def retention_key(row: dict, legacy_ids: set[str]) -> tuple:
    qid = row["query_id"]
    return (qid not in legacy_ids, int(row.get("rq2_candidate_index", -1)), qid)


def select_retained(accepted: dict, target: int, legacy_ids: set[str]) -> dict:
    if any(len(accepted[cell]) < target for cell in CELLS):
        raise ValueError("insufficient retained group pool")
    return {cell: sorted(accepted[cell].values(), key=lambda r: retention_key(r, legacy_ids))[:target]
            for cell in CELLS}


def validate_job(job: dict) -> None:
    inputs = read_rows(Path(job["input"]))
    expected = {r["query_id"] for r in inputs}
    out = Path(job["output"])
    rows = [r for name in DISPOSITIONS for r in read_rows(out / name)]
    completed = read_rows(out / "completed_variants.jsonl")
    ids, done = [r["query_id"] for r in rows], [r["query_id"] for r in completed]
    if (len(expected) != len(inputs) or len(expected) != job["count"] or
            len(ids) != len(expected) or set(ids) != expected or
            len(done) != len(expected) or set(done) != expected or
            any(int(r.get("variant_index", 0)) != 0 for r in rows + completed)):
        raise ValueError(f"incomplete or duplicate task dispositions: {out}")


def job_finished(job: dict) -> bool:
    try:
        validate_job(job)
    except (ValueError, KeyError):
        return False
    return True


def json_file(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def batches(root: Path) -> list[dict]:
    return json_file(root / "batches.json")["batches"]


def collect_accepted(root: Path, *, live: bool = False) -> dict:
    result = {cell: {r["query_id"]: r for r in read_rows(root / "legacy" / f"{cell[-1]}.jsonl")}
              for cell in CELLS}
    for batch in batches(root):
        for job in batch["jobs"]:
            cell = f"{job['profile']}/{job['arm']}"
            completed = {r["query_id"] for r in read_rows(Path(job["output"]) / "completed_variants.jsonl", live=live)}
            for row in read_rows(Path(job["output"]) / DISPOSITIONS[0], live=live):
                qid = row["query_id"]
                if qid not in completed:
                    continue
                if qid in result[cell]:
                    raise ValueError(f"duplicate generation event: {cell}/{qid}")
                result[cell][qid] = {
                    **row, "rq2_pair_keys": {panel: qid for panel in ("WS", "RCS")},
                    "rq2_candidate_index": int(qid.split(":", 1)[0].removeprefix("rq2_current_")),
                    "rq2_provenance": {"protocol": "current_unique_filtered",
                                       "input_path": job["input"], "accepted_path": job["output"],
                                       "batch": batch["index"]},
                }
    return result


def collect_status(root: Path) -> dict:
    manifest = json_file(root / "manifest.json")
    status = status_from_accepted(collect_accepted(root, live=True), manifest["target_per_group"])
    counts = {cell: {"completed": 0, "accepted": 0, "rejected": 0, "errors": 0} for cell in CELLS}
    for batch in batches(root):
        for job in batch["jobs"]:
            out, cell = Path(job["output"]), f"{job['profile']}/{job['arm']}"
            for key, filename in zip(("accepted", "rejected", "errors", "completed"),
                                     (*DISPOSITIONS, "completed_variants.jsonl")):
                counts[cell][key] += len(read_rows(out / filename, live=True))
    status.update(new_generation=counts, legacy_retained=manifest["legacy_retained"],
                  new_candidates_committed=sum(b["count"] for b in batches(root)))
    return status


def prepare(root: Path, paper_root: Path, pilot_root: Path, *, target: int,
            s_keep: int, seed: int, batch_size: int, shards: int, candidate_cap: int) -> dict:
    if root.exists():
        raise FileExistsError(f"refusing to overwrite existing run: {root}")
    if not 0 <= s_keep <= target or min(target, batch_size, shards, candidate_cap) < 1:
        raise ValueError("invalid target/reuse/batch/shards/cap")
    registered = load_registered_sources(paper_root, pilot_root)
    accepted = limit_legacy_reuse(registered["accepted"], s_keep=s_keep, seed=seed)
    model = resolve_model_config(load_yaml(REPO_ROOT / "configs/model.yaml"), task="instantiation")
    ModelConfig(**model)
    root.mkdir(parents=True)
    entries = []

    def record(path: Path) -> None:
        entries.append({"path": str(path.resolve()), "sha256": file_hash(path)})

    for arm in "WRCS":
        old_path = root / "legacy" / f"{arm}.jsonl"
        write_rows(old_path, list(accepted[f"default/{arm}"].values()))
        record(old_path)
        prior = root / "legacy/prior_inputs" / f"{arm}.jsonl"
        write_rows(prior, registered["prior_inputs"][arm])
        record(prior)
    source_paths = {
        "base_inputs": paper_root / "task_pool/base_inputs.jsonl",
        "skeletons": paper_root / "task_pool/skeletons.jsonl",
        "features": REPO_ROOT / "data/stage1/features.jsonl",
        "neo4j_docs": REPO_ROOT / "corpus/neo4j_official/embeddings/embedding_records.jsonl",
        "memgraph_docs": REPO_ROOT / "corpus/memgraph_official/embeddings/embedding_records.jsonl",
    }
    snapshot_paths = {}
    for name, source in source_paths.items():
        destination = root / "frozen" / f"{name}.jsonl"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        record(source)
        record(destination)
        snapshot_paths[name] = str(destination.resolve())
    write_json(root / "frozen/model.json", model)
    write_json(root / "frozen/pipeline.json", load_yaml(REPO_ROOT / "configs/pipeline.yaml"))
    shutil.copyfile(REPO_ROOT / "prompts/instantiate_case_system.txt", root / "frozen/instantiate_case_system.txt")
    for path in sorted((root / "frozen").iterdir()):
        if path.suffix != ".jsonl":
            record(path)
    code_paths = sorted((REPO_ROOT / "src").rglob("*.py")) + [
        REPO_ROOT / "src/stage2/documentation_example_review.json",
        Path(__file__).resolve(), REPO_ROOT / "scripts/rq2_current_inputs.py",
        REPO_ROOT / "scripts/rq2_existing_sources.py",
        REPO_ROOT / "scripts/run_rq2_complexity_pilot.py",
        REPO_ROOT / "scripts/build_random_conditioned_skeleton_ablation.py",
        REPO_ROOT / "scripts/build_within_report_skeleton_ablation.py",
    ]
    for source in dict.fromkeys(code_paths):
        destination = root / "code_snapshot" / source.relative_to(REPO_ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        record(source)
        record(destination)
    manifest = {
        "created_utc": utc_now(), "groups": list(CELLS), "target_per_group": target,
        "s_legacy_keep": s_keep, "sampling_seed": seed, "batch_size": batch_size,
        "shards_per_group": shards, "new_candidate_cap": candidate_cap,
        "concurrency_cap": 48, "database_execution": False,
        "generation_profile": "portable_crossdb", "complexity_profile": "default",
        "diversity_profile": "semantic_edges", "quality_retry_limit": 2,
        "variants_per_input": 1, "family_bias": "none",
        "new_protocol": "current unique unordered feature combinations per original skeleton; current documentation counterexample filtering",
        "legacy_retained": {cell: len(rows) for cell, rows in accepted.items()},
        "legacy_available_before_s_limit": {cell: len(registered["accepted"][cell]) for cell in CELLS},
        "source_registration": registered["panel_counts"], "notes": registered["notes"] + [
            "S legacy sample is fixed-seed uniform over registered accepted events, before observing any diversity metrics.",
            "Unused S legacy results are not deleted; all old inputs still seed the no-repeat combination history.",
            "Four independently retained pools are not thirty independent runs or thirty task-matched cohorts.",
            "Legacy/new protocols must be stratified for sensitivity analyses; no old calls are claimed to use current rules.",
            "No database execution; retained means existing static Stage3 acceptance, not semantic correctness.",
            "Duplicate query-text gating remains per shard as in the original generator, not global text uniqueness.",
        ],
        "frozen_files": entries, "legacy_source_files": registered["files"],
        "snapshot_paths": snapshot_paths, "python": sys.version,
        "package_versions": {name: importlib.metadata.version(name) for name in ("openai", "PyYAML")},
    }
    write_json(root / "manifest.json", manifest)
    write_json(root / "batches.json", {"batches": []})
    status = status_from_accepted(accepted, target)
    write_json(root / "progress.json", status)
    write_json(root / "state.json", {"status": "prepared", "updated_utc": utc_now()})
    return manifest


def make_builder(root: Path, manifest: dict) -> InputBuilder:
    paths = manifest["snapshot_paths"]
    prior = {arm: read_rows(root / "legacy/prior_inputs" / f"{arm}.jsonl") for arm in "WRCS"}
    committed = batches(root)
    for batch in committed:
        for arm in "WRCS":
            prior[arm].extend(read_rows(Path(batch["inputs"][arm])))
    extras = [dict(row, text=row.get("embedding_text") or row.get("text") or "", source_type="official_chunk")
              for name in ("neo4j_docs", "memgraph_docs") for row in read_rows(Path(paths[name]))]
    return InputBuilder(read_rows(Path(paths["base_inputs"])), read_rows(Path(paths["skeletons"])),
                        read_rows(Path(paths["features"])), extras,
                        seed=manifest["sampling_seed"], prior_inputs=prior,
                        start_index=sum(b["count"] for b in committed))


def commit_batch(root: Path, builder: InputBuilder, manifest: dict, active: list[str]) -> dict:
    committed = batches(root)
    start = sum(b["count"] for b in committed)
    count = min(manifest["batch_size"], manifest["new_candidate_cap"] - start)
    if count <= 0:
        raise RuntimeError("candidate cap reached before retained quotas; inspect progress, no automatic budget extension")
    batch_root = root / "batches" / f"batch_{len(committed):04d}"
    if batch_root.exists():
        raise FileExistsError(f"uncommitted batch exists; inspect rather than overwrite: {batch_root}")
    rows = builder.make_batch(start, count)
    inputs, frozen = {}, []
    for arm in "WRCS":
        path = batch_root / "inputs" / f"{arm}.jsonl"
        write_rows(path, rows[arm])
        inputs[arm] = str(path.resolve())
        frozen.append({"path": str(path.resolve()), "sha256": file_hash(path)})
    jobs = build_jobs(batch_root, rows, active, manifest["shards_per_group"])
    frozen.extend({"path": job["input"], "sha256": file_hash(Path(job["input"]))} for job in jobs)
    batch = {"index": len(committed), "start": start, "count": count, "inputs": inputs,
             "active_cells": active, "jobs": jobs, "frozen_files": frozen, "created_utc": utc_now()}
    write_json(batch_root / "manifest.json", batch)
    write_json(root / "batches.json", {"batches": committed + [batch]})
    return batch


def execute_jobs(root: Path, jobs: list[dict], workers: int, *, preflight: bool) -> None:
    cancelled = threading.Event()

    def execute(job: dict) -> dict:
        if cancelled.is_set() or (root / "STOP").exists():
            raise RuntimeError("stopped before launching a new shard")
        out = Path(job["output"])
        out.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        # Ignore inherited PYTHONPATH so imports really come from the frozen src.
        env.pop("PYTHONPATH", None)
        log = out / ("preflight.log" if preflight else "run.log")
        with log.open("a", encoding="utf-8") as handle:
            result = subprocess.run(stage3_command(job, root, preflight=preflight),
                                    cwd=root / "code_snapshot", env=env, stdout=handle,
                                    stderr=subprocess.STDOUT, timeout=86400)
        if result.returncode:
            cancelled.set()
            raise RuntimeError(f"Stage3 exited {result.returncode}; inspect {log}")
        summary = json_file(out / "summary.json")
        if (preflight and summary["errors"]) or (not preflight and summary["errors"] >= 10):
            cancelled.set()
            raise RuntimeError(f"operational errors; inspect {out / 'errors.jsonl'}")
        if not preflight:
            validate_job(job)
        return summary

    failures = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(execute, job): job for job in jobs}
        for future in as_completed(futures):
            job = futures[future]
            try:
                summary = future.result()
                print(json.dumps({"phase": "preflight" if preflight else "generation",
                                  "arm": job["arm"], "shard": job["shard"],
                                  "processed": summary["processed"], "accepted": summary["valid_cases"],
                                  "errors": summary["errors"]}), flush=True)
            except Exception as exc:
                cancelled.set()
                failures.append(str(exc))
    if failures:
        raise RuntimeError("; ".join(failures))


def finalize(root: Path, manifest: dict, accepted: dict) -> None:
    legacy_ids = {r["query_id"] for arm in "WRCS" for r in read_rows(root / "legacy" / f"{arm}.jsonl")}
    selected = select_retained(accepted, manifest["target_per_group"], legacy_ids)
    cohorts = {}
    for cell, rows in selected.items():
        arm = cell[-1]
        write_rows(root / "retained" / f"{arm}.jsonl", rows)
        rows = list(rows)
        random.Random(manifest["sampling_seed"]).shuffle(rows)
        sizes = []
        for start in range(0, len(rows), 1000):
            group = rows[start:start+1000]
            write_rows(root / "cohorts" / arm / f"cohort_{start//1000+1:02d}.jsonl", group)
            sizes.append(len(group))
        cohorts[arm] = sizes
    summary = collect_status(root)
    chosen_status = status_from_accepted({cell: {r["query_id"]: r for r in rows} for cell, rows in selected.items()}, manifest["target_per_group"])
    summary.update(selected_retained=chosen_status["retained"],
                   selected_matched_WS=chosen_status["matched_WS"], selected_matched_RCS=chosen_status["matched_RCS"],
                   cohort_sizes=cohorts, selection="all registered reused rows first, then ascending new candidate index; fixed-seed shuffled cohorts; no metric-based selection",
                   paired_cohort_claim=False, independent_run_claim=False)
    write_json(root / "summary.json", summary)


def pid_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        kernel = ctypes.windll.kernel32
        kernel.OpenProcess.restype = ctypes.c_void_p
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            kernel.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))
            return code.value == 259
        finally:
            kernel.CloseHandle(ctypes.c_void_p(handle))
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def stage3_process_ids(root: Path, records: list[dict]) -> list[int]:
    result = []
    root = root.resolve()
    for record in records:
        command = record.get("commandline") or ""
        try:
            tokens = [token.strip('"') for token in shlex.split(command, posix=os.name != "nt")]
        except ValueError:
            continue
        if not any(tokens[i:i+2] == ["-m", "src.cli.run_stage3"] for i in range(len(tokens)-1)):
            continue
        for index, token in enumerate(tokens[:-1]):
            if token in ("--input", "--output-dir") and Path(tokens[index+1]).resolve().is_relative_to(root):
                result.append(int(record["pid"]))
                break
    return result


def running_stage3(root: Path) -> list[int]:
    if os.name == "nt":
        command = ["powershell", "-NoProfile", "-Command",
                   "Get-CimInstance Win32_Process -Filter \"Name = 'python.exe' OR Name = 'pythonw.exe'\" | "
                   "Select-Object @{Name='pid';Expression={$_.ProcessId}},"
                   "@{Name='commandline';Expression={$_.CommandLine}} | ConvertTo-Json -Compress"]
        raw = subprocess.run(command, check=True, capture_output=True, text=True, timeout=30).stdout.strip()
        records = json.loads(raw) if raw else []
        if isinstance(records, dict):
            records = [records]
    else:
        raw = subprocess.run(["ps", "-eo", "pid=,args="], check=True, capture_output=True,
                             text=True, timeout=30).stdout
        records = [{"pid": line.strip().split(None, 1)[0], "commandline": line.strip().split(None, 1)[1]}
                   for line in raw.splitlines() if len(line.strip().split(None, 1)) == 2]
    return stage3_process_ids(root, records)


def acquire_lock(root: Path):
    # Hold the OS lock for the entire driver lifetime. The guard is never
    # unlinked, so two stale-lock reclaimers cannot lock different inodes.
    guard = (root / ".driver.guard").open("a+b")
    try:
        if guard.tell() == 0:
            guard.write(b"0")
            guard.flush()
        guard.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(guard.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(guard.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        guard.close()
        raise RuntimeError("another completion driver holds the OS lock") from exc
    path = root / "driver.lock"
    try:
        if path.exists():
            old = json_file(path)
            if pid_alive(int(old["pid"])):
                raise RuntimeError(f"another completion driver is running: PID {old['pid']}")
        children = running_stage3(root)
        if children:
            raise RuntimeError(f"Stage3 writers still running for this run: {children}; wait before resuming")
        if path.exists():
            path.replace(root / f"driver.stale.{os.getpid()}.json")
        with path.open("x", encoding="utf-8") as handle:
            json.dump({"pid": os.getpid(), "created_utc": utc_now()}, handle)
    except BaseException:
        guard.close()
        raise
    return guard


def release_lock(root: Path, guard) -> None:
    path = root / "driver.lock"
    try:
        if path.exists() and json_file(path).get("pid") == os.getpid():
            path.unlink()
    finally:
        guard.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--paper-root", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--pilot-root", type=Path, default=DEFAULT_PILOT)
    parser.add_argument("--target", type=int, default=30000)
    parser.add_argument("--s-legacy-keep", type=int, default=4000)
    parser.add_argument("--seed", type=int, default=20261013)
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--shards", type=int, default=12)
    parser.add_argument("--workers", type=int, default=48)
    parser.add_argument("--candidate-cap", type=int, default=60000)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--status-only", action="store_true")
    args = parser.parse_args()
    root = args.output_root.resolve()
    if args.status_only:
        print(json.dumps(collect_status(root), indent=2))
        return 0
    validate_workers(args.workers)
    manifest = json_file(root / "manifest.json") if args.resume else prepare(
        root, args.paper_root.resolve(), args.pilot_root.resolve(), target=args.target,
        s_keep=args.s_legacy_keep, seed=args.seed, batch_size=args.batch_size,
        shards=args.shards, candidate_cap=args.candidate_cap)
    verify_files(manifest["frozen_files"] + manifest["legacy_source_files"])
    for batch in batches(root):
        verify_files(batch["frozen_files"])
    lock = acquire_lock(root)
    try:
        builder = make_builder(root, manifest)
        if not batches(root):
            commit_batch(root, builder, manifest, list(CELLS))
        if args.prepare_only:
            print(json.dumps({"status": "prepared", "legacy_retained": manifest["legacy_retained"],
                              "first_batch_tasks": sum(j["count"] for j in batches(root)[0]["jobs"])}), flush=True)
            return 0
        cfg = json_file(root / "frozen/model.json")
        if cfg.get("provider") != "openai_compatible" or not os.getenv(cfg.get("api_key_env", "DEEPSEEK_API_KEY")):
            raise RuntimeError("real configured model and API key required; no mock substitution")
        preflight_path = root / "preflight.json"
        if not preflight_path.exists():
            write_json(root / "state.json", {"status": "preflight", "pid": os.getpid(), "updated_utc": utc_now()})
            jobs = [j for j in batches(root)[0]["jobs"] if j["shard"] == 1 and
                    not read_rows(Path(j["output"]) / "completed_variants.jsonl")]
            execute_jobs(root, jobs, min(args.workers, 4), preflight=True)
            # Existing errors cannot silently turn a failed preflight green on resume.
            first_jobs = [j for j in batches(root)[0]["jobs"] if j["shard"] == 1]
            if any(read_rows(Path(j["output"]) / "errors.jsonl") for j in first_jobs):
                raise RuntimeError("preflight has recorded errors; inspect before any bulk generation")
            write_json(preflight_path, {"status": "passed", "updated_utc": utc_now(), "jobs": len(first_jobs)})
        if args.preflight_only:
            write_json(root / "state.json", {"status": "preflight_passed", "updated_utc": utc_now()})
            print(json.dumps({"status": "preflight_passed"}), flush=True)
            return 0
        while True:
            if (root / "STOP").exists():
                write_json(root / "state.json", {"status": "stopped", "updated_utc": utc_now()})
                return 0
            verify_files(manifest["frozen_files"])
            accepted = collect_accepted(root)
            status = status_from_accepted(accepted, manifest["target_per_group"])
            write_json(root / "progress.json", collect_status(root))
            pending = [j for b in batches(root) for j in b["jobs"] if not job_finished(j)]
            if status["complete"] and not pending:
                finalize(root, manifest, accepted)
                write_json(root / "state.json", {"status": "complete", "updated_utc": utc_now()})
                return 0
            if not pending:
                batch = commit_batch(root, builder, manifest, status["active_cells"])
                pending = batch["jobs"]
            write_json(root / "state.json", {"status": "running", "pid": os.getpid(), "updated_utc": utc_now(),
                                             "workers": args.workers, "pending_jobs": len(pending),
                                             "retained": status["retained"]})
            execute_jobs(root, pending, args.workers, preflight=False)
    except Exception as exc:
        write_json(root / "state.json", {"status": "failed", "pid": os.getpid(), "updated_utc": utc_now(), "error": str(exc)})
        raise
    finally:
        release_lock(root, lock)


if __name__ == "__main__":
    raise SystemExit(main())
