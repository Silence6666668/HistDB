#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import json
import logging
import signal
import sys
import time
import warnings
from collections import Counter
from pathlib import Path
from time import perf_counter
from typing import Any, Iterable

from neo4j import GraphDatabase, Query

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.common.config import load_yaml
from src.stage4.executor import load_db_targets, split_cypher_statements


warnings.filterwarnings("ignore")
logging.getLogger("neo4j").setLevel(logging.ERROR)

CONNECTION_ERROR_PATTERNS = (
    "defunct connection",
    "failed to read from defunct connection",
    "failed to write to closed connection",
    "connection refused",
    "connection reset",
    "connection broken",
    "service unavailable",
    "unable to retrieve routing information",
    "failed to establish connection",
)


class CaseHardTimeoutError(TimeoutError):
    pass


def _raise_case_hard_timeout(signum: int, frame: Any) -> None:
    raise CaseHardTimeoutError("case hard timeout")


@contextlib.contextmanager
def case_hard_timeout(seconds: float | None) -> Iterable[None]:
    if not seconds or seconds <= 0 or not hasattr(signal, "SIGALRM"):
        yield
        return

    previous_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, _raise_case_hard_timeout)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fast coverage-only Stage4 replay with persistent Bolt drivers."
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--db-config", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--targets", nargs="*", default=None)
    parser.add_argument("--max-items", type=int, default=None)
    parser.add_argument(
        "--skip-items",
        type=int,
        default=0,
        help="Skip this many input cases before execution; useful for epoch-based replay.",
    )
    parser.add_argument("--query-timeout-sec", type=float, default=5.0)
    parser.add_argument(
        "--no-isolation",
        action="store_true",
        help="Do not run target pre_case queries before each case.",
    )
    parser.add_argument(
        "--run-cleanup",
        action="store_true",
        help="Run each case's cleanup_queries after the main query.",
    )
    parser.add_argument(
        "--fetch-limit",
        type=int,
        default=0,
        help="Number of records to fetch before consuming each result. Use 0 for coverage-only.",
    )
    parser.add_argument("--progress-interval", type=int, default=100)
    parser.add_argument(
        "--max-consecutive-connection-failures",
        type=int,
        default=20,
        help=(
            "Stop the current replay epoch after this many consecutive connection "
            "failures, so the outer runner can restart the DBMS."
        ),
    )
    parser.add_argument(
        "--case-hard-timeout-sec",
        type=float,
        default=0.0,
        help=(
            "Optional process-level hard timeout per case. On timeout the current "
            "epoch stops and the outer runner restarts the DBMS."
        ),
    )
    return parser.parse_args()


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            text = line.strip()
            if text:
                yield json.loads(text)


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path: Path, record: dict[str, Any]) -> None:
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def is_connection_failure(error: str | None) -> bool:
    if not error:
        return False
    lowered = error.lower()
    return any(pattern in lowered for pattern in CONNECTION_ERROR_PATTERNS)


def build_query(statement: str, timeout_sec: float | None) -> Query | str:
    if timeout_sec is None or timeout_sec <= 0:
        return statement
    return Query(statement, timeout=timeout_sec)


def run_statements(
    session: Any,
    statements: Iterable[str],
    timeout_sec: float | None,
    fetch_limit: int,
) -> int:
    executed = 0
    for raw in statements:
        for statement in split_cypher_statements(raw):
            result = session.run(build_query(statement, timeout_sec))
            if fetch_limit > 0:
                result.fetch(fetch_limit)
            result.consume()
            executed += 1
    return executed


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    results_path = output_dir / "coverage_only_results.jsonl"
    progress_path = output_dir / "progress.json"
    summary_path = output_dir / "summary.json"
    if results_path.exists():
        results_path.unlink()

    db_cfg = load_yaml(args.db_config)
    targets = load_db_targets(db_cfg, selected_names=args.targets)
    if not targets:
        raise ValueError(f"No enabled DB targets found in {args.db_config}")
    for target in targets:
        target.query_timeout_sec = args.query_timeout_sec

    def make_driver(target: Any) -> Any:
        return GraphDatabase.driver(
            target.uri,
            auth=target.auth(),
            warn_notification_severity="OFF",
            connection_timeout=min(max(args.query_timeout_sec, 1.0), 5.0),
            connection_acquisition_timeout=min(max(args.query_timeout_sec, 1.0), 5.0),
            max_transaction_retry_time=0,
        )

    drivers = {
        target.name: make_driver(target)
        for target in targets
    }

    status_counter: Counter[str] = Counter()
    target_counter: Counter[str] = Counter()
    total_cases_seen = 0
    total_case_target_runs = 0
    total_statements = 0
    consecutive_connection_failures = 0
    stopped_reason = None
    started_wall = time.time()
    started_perf = perf_counter()

    try:
        for input_index, case in enumerate(iter_jsonl(input_path)):
            if input_index < max(args.skip_items, 0):
                continue
            if args.max_items is not None and total_cases_seen >= args.max_items:
                break
            total_cases_seen += 1
            case_connection_failure = False
            case_non_connection_result = False
            case_hard_timed_out = False
            for target in targets:
                case_started = perf_counter()
                status = "passed"
                error = None
                phase = "pre_case"
                statements_executed = 0
                session_kwargs = {"database": target.database} if target.database else {}
                try:
                    with case_hard_timeout(args.case_hard_timeout_sec):
                        with drivers[target.name].session(**session_kwargs) as session:
                            if not args.no_isolation and target.pre_case_queries:
                                statements_executed += run_statements(
                                    session,
                                    target.pre_case_queries,
                                    target.query_timeout_sec,
                                    args.fetch_limit,
                                )
                            phase = "setup"
                            statements_executed += run_statements(
                                session,
                                case.get("setup_queries", []),
                                target.query_timeout_sec,
                                args.fetch_limit,
                            )
                            phase = "main"
                            statements_executed += run_statements(
                                session,
                                [str(case.get("main_query") or "")],
                                target.query_timeout_sec,
                                args.fetch_limit,
                            )
                            if args.run_cleanup:
                                phase = "cleanup"
                                statements_executed += run_statements(
                                    session,
                                    case.get("cleanup_queries", []),
                                    target.query_timeout_sec,
                                    args.fetch_limit,
                                )
                except CaseHardTimeoutError:
                    status = "failed"
                    error = f"{phase}: case hard timeout after {args.case_hard_timeout_sec}s"
                    case_hard_timed_out = True
                except Exception as exc:
                    status = "failed"
                    error = f"{phase}: {exc}"
                if status == "failed" and (
                    case_hard_timed_out or is_connection_failure(error)
                ):
                    try:
                        drivers[target.name].close()
                    except Exception:
                        pass
                    drivers[target.name] = make_driver(target)

                total_case_target_runs += 1
                total_statements += statements_executed
                status_counter[status] += 1
                target_counter[target.name] += 1
                if status == "failed" and is_connection_failure(error):
                    case_connection_failure = True
                else:
                    case_non_connection_result = True
                elapsed_ms = round((perf_counter() - case_started) * 1000.0, 3)
                append_jsonl(
                    results_path,
                    {
                        "query_id": case.get("query_id"),
                        "case_id": case.get("case_id"),
                        "target": target.name,
                        "status": status,
                        "error": error,
                        "elapsed_ms": elapsed_ms,
                        "statements_executed": statements_executed,
                    },
                )

            if case_connection_failure and not case_non_connection_result:
                consecutive_connection_failures += 1
            else:
                consecutive_connection_failures = 0

            if total_case_target_runs % max(args.progress_interval, 1) == 0:
                elapsed_sec = time.time() - started_wall
                rate = total_case_target_runs / elapsed_sec if elapsed_sec > 0 else 0.0
                write_json(
                    progress_path,
                    {
                        "status": "running",
                        "input_path": str(input_path),
                        "output_dir": str(output_dir),
                        "cases_seen": total_cases_seen,
                        "case_target_runs": total_case_target_runs,
                        "status_counts": dict(status_counter),
                        "target_counts": dict(target_counter),
                        "consecutive_connection_failures": consecutive_connection_failures,
                        "elapsed_sec": elapsed_sec,
                        "rate_case_target_runs_per_sec": rate,
                        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    },
                )
                print(
                    f"[coverage-only] runs={total_case_target_runs} "
                    f"cases={total_cases_seen} rate={rate:.2f}/s "
                    f"status={dict(status_counter)}",
                    flush=True,
                )
            if (
                args.max_consecutive_connection_failures > 0
                and consecutive_connection_failures >= args.max_consecutive_connection_failures
            ):
                stopped_reason = "consecutive_connection_failures"
                elapsed_sec = time.time() - started_wall
                rate = total_case_target_runs / elapsed_sec if elapsed_sec > 0 else 0.0
                write_json(
                    progress_path,
                    {
                        "status": "stopped",
                        "stopped_reason": stopped_reason,
                        "input_path": str(input_path),
                        "output_dir": str(output_dir),
                        "cases_seen": total_cases_seen,
                        "case_target_runs": total_case_target_runs,
                        "status_counts": dict(status_counter),
                        "target_counts": dict(target_counter),
                        "consecutive_connection_failures": consecutive_connection_failures,
                        "elapsed_sec": elapsed_sec,
                        "rate_case_target_runs_per_sec": rate,
                        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    },
                )
                print(
                    "[coverage-only] stopped: consecutive connection failures="
                    f"{consecutive_connection_failures}",
                    flush=True,
                )
                break
            if case_hard_timed_out:
                stopped_reason = "case_hard_timeout"
                elapsed_sec = time.time() - started_wall
                rate = total_case_target_runs / elapsed_sec if elapsed_sec > 0 else 0.0
                write_json(
                    progress_path,
                    {
                        "status": "stopped",
                        "stopped_reason": stopped_reason,
                        "input_path": str(input_path),
                        "output_dir": str(output_dir),
                        "cases_seen": total_cases_seen,
                        "case_target_runs": total_case_target_runs,
                        "status_counts": dict(status_counter),
                        "target_counts": dict(target_counter),
                        "consecutive_connection_failures": consecutive_connection_failures,
                        "elapsed_sec": elapsed_sec,
                        "rate_case_target_runs_per_sec": rate,
                        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    },
                )
                print("[coverage-only] stopped: case hard timeout", flush=True)
                break
    finally:
        for driver in drivers.values():
            driver.close()

    elapsed_sec = perf_counter() - started_perf
    summary = {
        "status": "stopped" if stopped_reason else "complete",
        "stopped_reason": stopped_reason,
        "input_path": str(input_path),
        "db_config": args.db_config,
        "output_dir": str(output_dir),
        "targets": [target.name for target in targets],
        "skip_items": max(args.skip_items, 0),
        "cases_seen": total_cases_seen,
        "case_target_runs": total_case_target_runs,
        "statements_executed": total_statements,
        "status_counts": dict(status_counter),
        "target_counts": dict(target_counter),
        "consecutive_connection_failures": consecutive_connection_failures,
        "isolation": not args.no_isolation,
        "run_cleanup": bool(args.run_cleanup),
        "query_timeout_sec": args.query_timeout_sec,
        "fetch_limit": args.fetch_limit,
        "elapsed_sec": elapsed_sec,
        "rate_case_target_runs_per_sec": (
            total_case_target_runs / elapsed_sec if elapsed_sec > 0 else 0.0
        ),
    }
    write_json(summary_path, summary)
    write_json(progress_path, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
