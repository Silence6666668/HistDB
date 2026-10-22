"""Read explicitly registered paper/pilot RQ2 artifacts without rerunning them.

These are legacy generation events. Their inputs and checks differ from the
current unique-combination selector and documentation counterexample policy.
Panel pairing is a sidecar; this loader does not impose a four-arm intersection.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any


LANES = {
    "W": "within_report_skeleton",
    "R": "random_feature_only",
    "C": "conditioned_feature_only",
    "S": "skeleton_feature",
}
CELLS = tuple(f"default/{arm}" for arm in LANES) + (
    "large_neo4j/S", "planner_stress_neo4j/S", "engine_stress_neo4j/S",
)
REPO_ROOT = Path(__file__).resolve().parents[1]


def _canonical_id(path: Path, query_id: str) -> str:
    token = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:12]
    return f"legacy_{token}:{query_id}"


def _input_signature(row: dict) -> str:
    # The pilot copies the frozen source row, adding only these bookkeeping fields.
    content = {key: value for key, value in row.items()
               if key != "query_id" and not key.startswith("pilot_")}
    payload = json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _index(rows: list[dict], path: Path) -> dict[str, dict]:
    result = {}
    for row in rows:
        qid = str(row.get("query_id") or "").strip()
        if not qid or qid in result:
            raise ValueError(f"empty or duplicate query_id in {path}: {qid}")
        result[qid] = row
    return result


def load_registered_sources(paper_root: Path, pilot_root: Path) -> dict[str, Any]:
    """Load only the two paper cohort source lists and the selected pilot jobs.

    Physical accepted-file identity prevents double counting shared S sources.
    Different WS/RCS generation files remain distinct even for the same seed and
    original query ID. Existing inputs, including rejected/error tasks, are
    retained for the caller's feature-combination ledger.
    """
    paper_root, pilot_root = Path(paper_root).resolve(), Path(pilot_root).resolve()
    accepted: dict[str, dict[str, dict]] = {cell: {} for cell in CELLS}
    prior_inputs: dict[str, list[dict]] = {arm: [] for arm in LANES}
    files: dict[str, str] = {}
    notes = [
        "Legacy rows use paper_legacy or pilot_legacy provenance; they do not claim the current selector, RAG policy, generator or quality-gate protocol.",
        "Pairing is panel-specific. Different WS/RCS S generation events are retained separately; shared physical accepted sources are counted once.",
    ]
    panel_counts: dict[str, Any] = {
        "paper_registered_accepted": {"WS": {}, "RCS": {}},
        "pilot_excluded_formal_overlap": {cell: 0 for cell in CELLS},
        "registered_sources": [],
    }
    row_cache: dict[Path, list[dict]] = {}
    prior_seen: set[tuple[str, Path]] = set()
    formal_inputs: dict[str, set[tuple[int, str, str]]] = {arm: set() for arm in LANES}
    physical_sources: set[Path] = set()

    def record(path: Path) -> Path:
        path = path.resolve()
        if str(path) not in files:
            files[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        return path

    def read_json(path: Path) -> dict:
        return json.loads(record(path).read_text(encoding="utf-8-sig"))

    def read_rows(path: Path) -> list[dict]:
        path = record(path)
        if path not in row_cache:
            with path.open(encoding="utf-8-sig") as handle:
                row_cache[path] = [json.loads(line) for line in handle if line.strip()]
        return row_cache[path]

    def input_path(value: str) -> Path:
        path = Path(value)
        return path.resolve() if path.is_absolute() else (REPO_ROOT / path).resolve()

    def add_prior(arm: str, path: Path, rows: list[dict], protocol: str) -> None:
        identity = (arm, path.resolve())
        if identity in prior_seen:
            return
        prior_seen.add(identity)
        for original in rows:
            row = dict(original)
            row["rq2_provenance"] = {"protocol": protocol, "input_path": str(path.resolve())}
            prior_inputs[arm].append(row)

    def completed(shards: list[Path], expected: set[str]) -> bool:
        if not shards:
            return False
        ledger = []
        for shard in shards:
            path = shard / "completed_variants.jsonl"
            if not path.is_file():
                return False
            ledger.extend(read_rows(path))
        indexed = _index(ledger, shards[0] / "completed_variants.jsonl")
        if set(indexed) != expected or any(int(row.get("variant_index", 0)) != 0 for row in ledger):
            raise ValueError(f"incomplete or unexpected completion ledger: {shards[0]}")
        return True

    def put(cell: str, path: Path, row: dict, original_qid: str,
            protocol: str, seed: int, pairs: dict[str, str], inp: Path) -> None:
        case_id = _canonical_id(path, str(row["query_id"]))
        existing = accepted[cell].get(case_id)
        if existing is not None:
            for panel, pair_key in pairs.items():
                if panel in existing["rq2_pair_keys"] and existing["rq2_pair_keys"][panel] != pair_key:
                    raise ValueError(f"conflicting panel key for {case_id}")
                existing["rq2_pair_keys"][panel] = pair_key
            return
        result = copy.deepcopy(row)
        result.update(query_id=case_id, original_query_id=original_qid, rq2_pair_keys=dict(pairs))
        result["rq2_provenance"] = {
            "protocol": protocol, "accepted_path": str(path.resolve()),
            "input_path": str(inp.resolve()), "generation_seed": seed,
            "original_accepted_query_id": str(row["query_id"]),
        }
        accepted[cell][case_id] = result

    for panel in ("WS", "RCS"):
        cohort_manifest = paper_root / "analysis_five_disjoint_cohorts_20260926" / panel / "cohorts" / "summary.json"
        if not cohort_manifest.is_file():
            notes.append(f"Excluded missing paper panel manifest: {cohort_manifest}")
            continue
        for source in read_json(cohort_manifest).get("sources", []):
            arm, seed = source["arm"], int(source["seed"])
            if arm not in LANES:
                raise ValueError(f"unknown arm: {arm}")
            path = input_path(source["path"])
            lane_root = path.parent.parent
            manifest_path = lane_root / "run_manifest.json"
            summary_path = path.parent / "summary.json"
            if not all(item.is_file() for item in (path, manifest_path, summary_path)):
                notes.append(f"Excluded partial paper source without accepted/manifest/summary: {path}")
                continue
            manifest, summary = read_json(manifest_path), read_json(summary_path)
            if int(manifest.get("quality_retry_limit", -1)) != 2:
                notes.append(f"Excluded non-QR2 paper source: {path}")
                continue
            if int(manifest.get("variants_per_input", 1)) != 1 or manifest.get("complexity_profile", "default") != "default":
                raise ValueError(f"incompatible paper source protocol: {manifest_path}")
            if manifest.get("input_path"):
                inp = input_path(manifest["input_path"])
            else:
                sub = "inputs_within_report_strict" if arm == "W" else "inputs"
                inp = paper_root / sub / f"seed_{seed}" / f"{LANES[arm]}.jsonl"
            if not inp.is_file():
                notes.append(f"Excluded missing paper input: {inp}")
                continue
            inputs = read_rows(inp)
            indexed_inputs = _index(inputs, inp)
            expected = len(inputs)
            if summary.get("input_records") != expected or summary.get("processed") != expected:
                notes.append(f"Excluded unfinished paper source: {path}")
                continue
            if sum(int(summary.get(key, 0)) for key in ("valid_cases", "rejected_cases", "errors")) != expected:
                raise ValueError(f"paper disposition count mismatch: {summary_path}")
            shards = [input_path(shard["dir"]) for shard in summary.get("shards", [])]
            if not completed(shards, set(indexed_inputs)):
                notes.append(f"Excluded paper source without completion ledgers: {path}")
                continue
            rows = read_rows(path)
            indexed_rows = _index(rows, path)
            if len(rows) != int(source["count"]) or len(rows) != int(summary["valid_cases"]):
                raise ValueError(f"paper accepted count mismatch: {path}")
            if set(indexed_rows) - set(indexed_inputs):
                raise ValueError(f"accepted query_id absent from inputs: {path}")
            if any(int(row.get("variant_index", 0)) != 0 for row in rows):
                raise ValueError(f"unexpected accepted variant: {path}")
            add_prior(arm, inp, inputs, "paper_legacy")
            for qid, row in indexed_inputs.items():
                formal_inputs[arm].add((seed, qid, _input_signature(row)))
            for row in rows:
                qid = str(row["query_id"])
                put(f"default/{arm}", path, row, qid, "paper_legacy", seed,
                    {panel: f"seed_{seed}:{qid}"}, inp)
            totals = panel_counts["paper_registered_accepted"][panel]
            totals[arm] = totals.get(arm, 0) + len(rows)
            physical_sources.add(path.resolve())
            panel_counts["registered_sources"].append({
                "panel": panel, "arm": arm, "seed": seed, "accepted_path": str(path),
                "input_path": str(inp), "accepted": len(rows), "inputs": expected,
                "protocol": "paper_legacy",
            })

    pilot_manifest_path, pilot_summary_path = pilot_root / "manifest.json", pilot_root / "summary.json"
    if pilot_manifest_path.is_file() and pilot_summary_path.is_file():
        manifest, summary = read_json(pilot_manifest_path), read_json(pilot_summary_path)
        if int(manifest.get("quality_retry_limit", -1)) != 2:
            notes.append("Excluded pilot with non-QR2 protocol")
        else:
            cells = {(cell["profile"], cell["arm"]): cell for cell in summary.get("cells", [])}
            jobs = manifest.get("jobs", [])
            for profile, arm in dict.fromkeys((job["profile"], job["arm"]) for job in jobs):
                cell_id = f"{profile}/{arm}"
                if cell_id not in accepted:
                    raise ValueError(f"unexpected pilot cell: {cell_id}")
                cell = cells.get((profile, arm))
                main_summary = pilot_root / "cells" / profile / arm / "stage3_main" / "summary.json"
                if not cell or not main_summary.is_file():
                    notes.append(f"Excluded partial pilot cell: {cell_id}")
                    continue
                local_summary = read_json(main_summary)
                for key in ("input_tasks", "completed", "accepted", "rejected", "errors"):
                    if local_summary.get(key) != cell.get(key):
                        raise ValueError(f"conflicting pilot cell summary: {main_summary}/{key}")
                inp = pilot_root / "inputs" / f"{arm}.jsonl"
                inputs = read_rows(inp)
                indexed_inputs = _index(inputs, inp)
                expected = len(inputs)
                if cell.get("completed") != expected or cell.get("input_tasks") != expected:
                    notes.append(f"Excluded unfinished pilot cell: {cell_id}")
                    continue
                if sum(int(cell.get(key, 0)) for key in ("accepted", "rejected", "errors")) != expected:
                    raise ValueError(f"pilot disposition count mismatch: {cell_id}")
                selected_jobs = [job for job in jobs if (job["profile"], job["arm"]) == (profile, arm)]
                shards = [input_path(job["output"]) for job in selected_jobs]
                if not completed(shards, set(indexed_inputs)):
                    notes.append(f"Excluded pilot without completion ledgers: {cell_id}")
                    continue
                grouped_rows = []
                source_rows = []
                for shard in shards:
                    path = shard / "instantiated_cases.jsonl"
                    rows = read_rows(path) if path.is_file() else []
                    grouped_rows.extend(rows)
                    source_rows.extend((path, row) for row in rows)
                indexed_rows = _index(grouped_rows, pilot_root / cell_id)
                if len(grouped_rows) != int(cell["accepted"]) or set(indexed_rows) - set(indexed_inputs):
                    raise ValueError(f"pilot accepted count/inputs mismatch: {cell_id}")
                if any(int(row.get("variant_index", 0)) != 0 for row in grouped_rows):
                    raise ValueError(f"unexpected pilot accepted variant: {cell_id}")
                add_prior(arm, inp, inputs, "pilot_legacy")
                for path, row in source_rows:
                    raw_qid = str(row["query_id"])
                    input_row = indexed_inputs[raw_qid]
                    original_qid = str(input_row["pilot_original_query_id"])
                    seed = int(input_row["pilot_generation_seed"])
                    if profile == "default" and (seed, original_qid, _input_signature(input_row)) in formal_inputs[arm]:
                        panel_counts["pilot_excluded_formal_overlap"][cell_id] += 1
                        continue
                    pairs = {panel: f"pilot:{raw_qid}" for panel in ("WS", "RCS")} if profile == "default" else {}
                    put(cell_id, path, row, original_qid, "pilot_legacy", seed, pairs, inp)
    elif pilot_manifest_path.exists() or pilot_root.exists():
        notes.append(f"Excluded pilot without manifest and final summary: {pilot_root}")

    panel_counts["formal_unique_physical_sources"] = len(physical_sources)
    for panel in ("WS", "RCS"):
        panel_counts[panel] = {arm: len({row["rq2_pair_keys"][panel]
                                      for row in accepted[f"default/{arm}"].values()
                                      if panel in row["rq2_pair_keys"]}) for arm in LANES}
    return {
        "accepted": accepted, "prior_inputs": prior_inputs,
        "files": [{"path": path, "sha256": digest} for path, digest in sorted(files.items())],
        "legacy_ids": {case_id for rows in accepted.values() for case_id in rows},
        "panel_counts": panel_counts, "notes": notes,
    }
