#!/usr/bin/env python3
"""Delta-debug every diverging ablation candidate down to a minimal trigger.

The de-duplication question "how many distinct bugs did this run find?" cannot
be answered from the raw queries: the generator emits a fresh literal layout
for every case, so surface shapes are almost injective.  The defensible unit is
the *minimal trigger* -- the smallest query that still produces the same
disagreement (same kind of divergence, same set of engines on the wrong side).

This script, for each candidate:

  1. re-runs the original case and records its divergence signature
     (crash / row_count / value + the set of disagreeing engines);
  2. runs classic ddmin over removable units -- the setup statements and the
     top-level clauses of the main query, then the individual RETURN items;
  3. keeps only the units whose removal loses the divergence, and stops when no
     single unit or chunk can be dropped any more;
  4. writes the retained units, the minimized query and its normalized shape.

Candidates whose minimized shape is identical are the same bug by construction.
Results are appended to a JSONL checkpoint so a long run can be resumed.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.common.config import load_yaml  # noqa: E402
from src.stage3e_diversity.metrics import normalize_cypher_shape  # noqa: E402
from src.stage4.executor import execute_case_against_target, load_db_targets  # noqa: E402
from src.stage4.executor import execute_case_against_target_with_hard_timeout  # noqa: E402
from src.stage4.executor_age import execute_case_against_target_age  # noqa: E402
from src.stage4.executor_arcadedb import execute_case_against_target_arcadedb  # noqa: E402
from src.stage4.executor_falkordb import execute_case_against_target_falkordb  # noqa: E402
from scripts.review_candidates import (  # noqa: E402
    diff_kind,
    has_non_scalar,
    normalized_rows,
)
from src.reproduction.oracles import _multiset_matches, _values_equal  # noqa: E402
from scripts.triage_ablation_differential import CRASH_RE  # noqa: E402

LANES = ["skeleton_feature", "skeleton_only", "feature_only", "random_feature"]

CLAUSE_START = re.compile(
    r"(OPTIONAL\s+MATCH|DETACH\s+DELETE|ON\s+CREATE\s+SET|ON\s+MATCH\s+SET"
    r"|UNION\s+ALL|ORDER\s+BY|MATCH|UNWIND|WITH|WHERE|CALL|CREATE|MERGE|SET"
    r"|DELETE|REMOVE|RETURN|UNION|FOREACH|SKIP|LIMIT|YIELD)\b",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------- #
# query splitting
# --------------------------------------------------------------------------- #
def split_top_level(text: str, pattern: re.Pattern, keep_separator: bool = True) -> list[str]:
    """Split `text` at `pattern` matches that sit outside any bracket/string.

    With ``keep_separator`` the matched text starts the following unit (used for
    clause keywords, which must stay attached to their clause).  With
    ``keep_separator=False`` the match is dropped (used for separators such as
    the commas of a RETURN projection list).
    """
    units: list[str] = []
    depth = 0
    quote: str | None = None
    start = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "'\"`":
            quote = ch
            i += 1
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        elif depth == 0 and i > start:
            m = pattern.match(text, i)
            if m:
                piece = text[start:i].strip()
                if piece:
                    units.append(piece)
                start = i if keep_separator else m.end()
                i = m.end()
                continue
        i += 1
    tail = text[start:].strip()
    if tail:
        units.append(tail)
    return units


def clause_units(main_query: str) -> list[str]:
    return split_top_level(main_query, CLAUSE_START)


RETURN_ITEM_SEP = re.compile(r",")


def return_item_units(main_query: str) -> tuple[int, list[str]]:
    """Locate the final RETURN and split its projection list into units."""
    clauses = clause_units(main_query)
    for idx, clause in enumerate(clauses):
        if re.match(r"\s*RETURN\b", clause, re.IGNORECASE):
            body = re.sub(r"^\s*RETURN\s+", "", clause, flags=re.IGNORECASE)
            if re.match(r"^\s*DISTINCT\b", body, re.IGNORECASE):
                body = re.sub(r"^\s*DISTINCT\s+", "", body, flags=re.IGNORECASE)
            items = [p.strip() for p in
                     split_top_level(body, RETURN_ITEM_SEP, keep_separator=False)
                     if p.strip()]
            if len(items) > 1:
                return idx, items
    return -1, []


# --------------------------------------------------------------------------- #
# divergence signature
# --------------------------------------------------------------------------- #
def signature(results: list[dict]) -> tuple | None:
    """Order-independent divergence key for one case across all targets.

    The earlier version keyed on "targets whose rows differ from the first
    target", which makes the outlier set depend on the iteration order: the same
    two engines produce a different signature depending on which one is listed
    first, so ddmin ends up rejecting perfectly good reductions.  Here the
    reference is the *majority* row multiset (largest group of engines that
    agree with each other), which is stable under permutation.
    """
    failed = [r for r in results if r.get("status") != "passed"]
    if any(CRASH_RE.search(str(r.get("error") or "")) for r in failed):
        # Fingerprint *how* each engine failed, so that ddmin cannot "minimize"
        # a real defect into a different one (e.g. turning an internal error
        # into a plain parse error by deleting the MATCH that binds a variable).
        engines = tuple(sorted((r["target"], error_class(str(r.get("error") or "")))
                               for r in failed))
        return ("crash", engines)
    passed = [r for r in results if r.get("status") == "passed"]
    if len(passed) < 2 or len(passed) != len(results):
        return None
    rows_by_target = {r["target"]: (normalized_rows(r) or []) for r in passed}
    if has_non_scalar(rows_by_target):
        return None

    def same(a: list, b: list) -> bool:
        return _multiset_matches(a, b, lambda x, y: _values_equal(x, y, 0.0))

    best_members: tuple[str, ...] = ()
    for t, rows in rows_by_target.items():
        members = tuple(sorted(u for u, other in rows_by_target.items() if same(other, rows)))
        if len(members) > len(best_members) or (
                len(members) == len(best_members) and members < best_members):
            best_members = members
    disagreeing = tuple(sorted(set(rows_by_target) - set(best_members)))
    if not disagreeing:
        return ("agree", ())
    return (diff_kind(rows_by_target), disagreeing)


def error_class(text: str) -> str:
    """Coarse error taxonomy, stable across engines and query rewrites."""
    t = text.lower()
    if "syntax error" in t or "parsing error" in t or "invalid input" in t \
            or "failed to parse" in t or "mismatched input" in t:
        return "parse_error"
    if "shortestpath" in t or "shortest path" in t:
        return "shortestpath"
    if "outofmemory" in t or "heap space" in t or "memory limit" in t \
            or "invalid memory alloc" in t or "alloc" in t and "request size" in t:
        return "oom"
    if "timed out" in t or "timeout" in t:
        return "timeout"
    if "connection closed" in t or "connection reset" in t or "broken pipe" in t:
        return "conn_closed"
    if "recovery mode" in t or "not yet accepting connections" in t or "connection refused" in t:
        return "service_down"
    if "does not support" in t or "not supported" in t or "unsupported" in t:
        return "unsupported"
    return "other"


class Runner:
    """Runs one case against the selected targets and returns the signature."""

    def __init__(self, targets: list, workers: int, max_parallel: int = 6):
        self.targets = targets
        self.pool = ThreadPoolExecutor(max_workers=min(workers, max_parallel))
        self.lock = threading.Lock()
        self.executions = 0
        self.use_hard_timeout = False
        # Neo4j/Memgraph occasionally leave a transaction holding storage locks,
        # after which every later query on that engine blocks forever.  A
        # per-target watchdog plus a container restart is far cheaper than
        # spawning a process for every trial.
        self.per_target_timeout = 45.0
        self.restarts = 0

    def _dispatch(self, case: dict, target) -> dict:
        """Same engine mapping as src.cli.run_stage4, minus the subprocess
        hard-timeout wrapper (too costly to spawn per minimization trial)."""
        if target.engine == "age":
            return execute_case_against_target_age(case, target, run_cleanup=True)
        if target.engine == "arcadedb":
            return execute_case_against_target_arcadedb(case, target, run_cleanup=True)
        if target.engine == "falkordb":
            return execute_case_against_target_falkordb(case, target, run_cleanup=True)
        if self.use_hard_timeout:
            return execute_case_against_target_with_hard_timeout(case, target, run_cleanup=True)
        return execute_case_against_target(case, target, run_cleanup=True)

    def _restart(self, target) -> None:
        container = "histdb_abl_" + target.name
        try:
            subprocess.run(["docker", "restart", container],
                           capture_output=True, timeout=120)
            self.restarts += 1
        except Exception:  # noqa: BLE001
            pass
        time.sleep(5)

    def _run_once(self, case: dict) -> tuple[list[dict], list]:
        pairs = [(t, self.pool.submit(self._dispatch, case, t)) for t in self.targets]
        results: list[dict] = []
        timed_out = []
        for t, fut in pairs:
            try:
                results.append(fut.result(timeout=self.per_target_timeout))
            except FuturesTimeout:
                timed_out.append(t)
                results.append({"target": t.name, "status": "failed",
                                "error": f"watchdog: no reply within "
                                         f"{self.per_target_timeout:.0f}s"})
            except Exception as exc:  # noqa: BLE001
                results.append({"target": t.name, "status": "failed", "error": str(exc)})
        return results, timed_out

    def run(self, case: dict, attempts: int = 2) -> tuple | None:
        for attempt in range(attempts):
            results, timed_out = self._run_once(case)
            if not timed_out:
                with self.lock:
                    self.executions += 1
                return signature(results)
            # A stuck engine poisons the whole run until it is restarted; the
            # abandoned worker thread dies when its connection drops.
            for t in timed_out:
                self._restart(t)
        with self.lock:
            self.executions += 1
        return None


# --------------------------------------------------------------------------- #
# ddmin
# --------------------------------------------------------------------------- #
def chunks(items: list, n: int) -> list[list]:
    if n <= 1:
        return [list(items)]
    size = max(1, len(items) // n)
    out = [items[i:i + size] for i in range(0, len(items), size)]
    if len(out) > n:
        out = out[:n - 1] + [sum(out[n - 1:], [])]
    return out


def ddmin(units: list, test, budget: int, stats: dict) -> list:
    """Classic ddmin: shrink `units` while `test(units)` keeps holding.

    ``budget`` caps the predicate evaluations *for this call*; the running total
    is accumulated in ``stats`` so a run can be costed afterwards.
    """
    units = list(units)
    n = 2
    spent = 0
    while len(units) >= 2 and spent < budget:
        parts = chunks(units, n)
        shrunk = False
        for part in parts:
            if not part:
                continue
            complement = [u for u in units if u not in part]
            spent += 1
            stats["tests"] += 1
            if complement and test(complement):
                units = complement
                n = max(2, n - 1)
                shrunk = True
                break
        if not shrunk:
            if n >= len(units):
                break
            n = min(len(units), n * 2)
    return units


class TimeBudgetExceeded(Exception):
    """Raised by the guarded predicate when a candidate runs out of wall clock."""


def greedy_shrink(units: list, test, budget: int, stats: dict) -> list:
    """Delete one unit at a time until no single deletion keeps the bug.

    ddmin alone stops early on small unit lists (it never tries removing *all*
    units once ``n`` reaches the list length), which leaves purely decorative
    clauses -- MERGEs of nodes that only appear in the projection, or a setup
    statement the main query re-creates anyway -- in the "minimal" query.
    """
    units = list(units)
    spent = 0
    changed = True
    while changed and spent < budget:
        changed = False
        i = 0
        while i < len(units) and spent < budget:
            trial = units[:i] + units[i + 1:]
            spent += 1
            stats["tests"] += 1
            if trial and test(trial):
                units = trial
                changed = True
            else:
                i += 1
    return units


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exec-root", required=True,
                    help=".../exec_sample4000 directory holding <lane>/execution_results.jsonl")
    ap.add_argument("--db-config", default="configs/db_targets_ablation_5db_plain.yaml")
    ap.add_argument("--output", required=True, help="JSONL checkpoint path")
    ap.add_argument("--only", nargs="*", default=None, help="restrict to these lanes")
    ap.add_argument("--limit", type=int, default=0, help="minimize at most N candidates")
    ap.add_argument("--max-tests", type=int, default=60,
                    help="cap predicate evaluations per phase (setup / clauses / RETURN items)")
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--debug", action="store_true",
                    help="print every rejected deletion with its signature")
    ap.add_argument("--candidate-timeout-sec", type=float, default=180.0,
                    help="wall-clock budget per candidate; on expiry the reduction "
                         "achieved so far is kept and the record is flagged")
    args = ap.parse_args()

    exec_root = Path(args.exec_root)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    done: set[tuple] = set()
    if out_path.exists():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                # a run killed mid-write can leave a truncated final line
                continue
            done.add((rec["lane"], rec["query_id"], rec["case_id"]))
    print(f"resume: {len(done)} candidates already minimized", flush=True)

    cfg = load_yaml(args.db_config)
    all_targets = load_db_targets(cfg)
    by_name = {t.name: t for t in all_targets}
    runner = Runner(all_targets, args.workers)

    # candidate list -------------------------------------------------------
    import csv
    cand_csv = exec_root / "final_dedup.csv"
    rows = list(csv.DictReader(open(cand_csv, encoding="utf-8")))
    # `key` is "<query_id>::<case_id>"; query_id itself may contain no '::'.
    for r in rows:
        parts = (r.get("key") or "").split("::")
        if len(parts) >= 2:
            r["query_id"] = "::".join(parts[:-1])
            r["case_id"] = parts[-1]
        else:
            r["query_id"] = r.get("key") or ""
            r["case_id"] = ""
    lanes = [l for l in LANES if not args.only or l in args.only]
    rows = [r for r in rows if r["lane"] in lanes]
    if args.limit:
        rows = rows[:args.limit]
    print(f"{len(rows)} candidates to minimize", flush=True)

    case_cache: dict[str, dict[str, dict]] = {}

    def load_cases(lane: str) -> dict[str, dict]:
        if lane not in case_cache:
            idx = {}
            with (exec_root / lane / "cases.jsonl").open(encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        c = json.loads(line)
                        idx[f"{c.get('query_id')}::{c.get('case_id')}"] = c
            case_cache[lane] = idx
        return case_cache[lane]

    t0 = time.time()
    processed = 0
    with out_path.open("a", encoding="utf-8") as fh:
        for row in rows:
            key = (row["lane"], row["query_id"], row["case_id"])
            if key in done:
                continue
            cases = load_cases(row["lane"])
            case = cases.get(f"{row['query_id']}::{row['case_id']}")
            if case is None:
                print(f"  ! missing case {key}", flush=True)
                continue

            original = {
                "query_id": case.get("query_id"),
                "case_id": case.get("case_id"),
                "setup_queries": list(case.get("setup_queries") or []),
                "main_query": case.get("main_query", ""),
                "cleanup_queries": list(case.get("cleanup_queries") or []),
            }
            # every candidate starts from the full target set: the previous
            # candidate narrowed it to the engines that disagreed for *it*.
            runner.targets = all_targets
            base_sig = None
            for _ in range(3):
                base_sig = runner.run(original)
                if base_sig is not None and base_sig[0] != "agree":
                    break
                time.sleep(0.5)
            if base_sig is None or base_sig[0] == "agree":
                rec = {"lane": row["lane"], "query_id": row["query_id"],
                       "case_id": row["case_id"], "status": "no_repro",
                       "base_signature": list(base_sig) if base_sig else None}
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fh.flush()
                continue

            # Keep all five engines for every trial: the signature is defined
            # relative to the majority, so dropping the non-disagreeing engines
            # would make the outlier set ambiguous.
            runner.targets = all_targets

            setup = list(original["setup_queries"])
            clauses = clause_units(original["main_query"])
            stats = {"tests": 0}

            # A handful of candidates make every trial time out (10 s each), so
            # a single case can otherwise take tens of minutes.  Keep whatever
            # reduction has been achieved when the wall clock runs out.
            deadline = time.monotonic() + args.candidate_timeout_sec
            state = {"setup": setup, "clauses": clauses}

            def guard(fn):
                def inner(units: list) -> bool:
                    if time.monotonic() > deadline:
                        raise TimeBudgetExceeded()
                    return fn(units)
                return inner

            def build(setup_units: list, clause_list: list) -> dict:
                return {
                    "query_id": original["query_id"], "case_id": original["case_id"],
                    "setup_queries": list(setup_units),
                    "main_query": " ".join(clause_list),
                    "cleanup_queries": original["cleanup_queries"],
                }

            def test_setup(sub: list) -> bool:
                got = runner.run(build(sub, state["clauses"]))
                if args.debug and got != base_sig:
                    print(f"      [setup] drop -> {got}", flush=True)
                return got == base_sig

            def test_clauses(sub: list) -> bool:
                got = runner.run(build(state["setup"], sub))
                if args.debug and got != base_sig:
                    print(f"      [clause] drop -> {got}  :: {' '.join(sub)[:110]}", flush=True)
                return got == base_sig

            # Prune the projection first, then the clauses, then the projection
            # again: dropping a RETURN item is what makes its producing clauses
            # removable in the next pass.
            def shrink_projection() -> None:
                # Find the RETURN clause in the *list* (re-splitting the joined
                # text can produce a different number of units, which used to
                # index out of range).
                current_clauses = state["clauses"]
                ri = next((i for i, c in enumerate(current_clauses)
                           if re.match(r"\s*RETURN\b", c, re.I)), -1)
                if ri < 0:
                    return
                _, items = return_item_units(" ".join(current_clauses))
                if len(items) < 2:
                    return
                distinct = bool(re.match(r"\s*RETURN\s+DISTINCT\b", current_clauses[ri], re.I))
                prefix = "DISTINCT " if distinct else ""

                def test_items(sub_items: list) -> bool:
                    trial = list(current_clauses)
                    trial[ri] = f"RETURN {prefix}{', '.join(sub_items)}"
                    return runner.run(build(state["setup"], trial)) == base_sig

                items = ddmin(items, guard(test_items), args.max_tests, stats)
                items = greedy_shrink(items, guard(test_items), args.max_tests, stats)
                items = [it for it in items if it.strip()] or items
                out = list(current_clauses)
                out[ri] = f"RETURN {prefix}{', '.join(items)}"
                state["clauses"] = out

            truncated = False
            try:
                state["setup"] = ddmin(state["setup"], guard(test_setup),
                                       args.max_tests, stats)
                state["setup"] = greedy_shrink(state["setup"], guard(test_setup),
                                               args.max_tests, stats)
                shrink_projection()
                state["clauses"] = ddmin(state["clauses"], guard(test_clauses),
                                         args.max_tests, stats)
                state["clauses"] = greedy_shrink(state["clauses"], guard(test_clauses),
                                                 args.max_tests, stats)
                shrink_projection()
            except TimeBudgetExceeded:
                truncated = True

            setup = state["setup"]
            clauses = state["clauses"]

            main = " ".join(clauses)
            rec = {
                "lane": row["lane"], "query_id": row["query_id"],
                "case_id": row["case_id"], "status": "ok",
                "base_signature": [base_sig[0], sorted(base_sig[1])],
                "kept_setup": setup,
                "kept_clauses": clauses,
                "minimized_setup_count": len(setup),
                "minimized_clause_count": len(clauses),
                "minimized_query": ((" ".join(setup) + " ") if setup else "") + main,
                "minimized_main_shape": normalize_cypher_shape(main),
                "tests_used": stats["tests"],
                "time_truncated": truncated,
            }
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            processed += 1
            print(f"[{processed}/{len(rows)}] {row['lane']:18} {base_sig[0]:9} "
                  f"{len(setup)} setup + {len(clauses)} clauses "
                  f"({stats['tests']} tests, {time.time()-t0:.0f}s)", flush=True)

    print(f"\ndone: {processed} minimized, {time.time()-t0:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
