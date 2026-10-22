from __future__ import annotations

import csv
import io
import json
import subprocess
from time import perf_counter
from typing import Any, Callable, Dict, Iterable, List

from src.stage4.executor import DbTarget, split_cypher_statements


class MemgraphCliError(RuntimeError):
    pass


CommandRunner = Callable[
    [list[str], str, float | None], subprocess.CompletedProcess[str]
]


def _default_command_runner(
    args: list[str], input_text: str, timeout_sec: float | None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        input=input_text,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_sec,
    )


def _decode_csv_value(value: str) -> Any:
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


def _parse_csv_output(
    stdout: str, fetch_limit: int
) -> tuple[list[str], list[dict[str, Any]], bool]:
    rows = list(csv.reader(io.StringIO(stdout)))
    if not rows:
        return [], [], False
    keys = [str(value) for value in rows[0]]
    data_rows = rows[1:]
    records = [
        {
            keys[index] if index < len(keys) else f"col_{index}": _decode_csv_value(
                value
            )
            for index, value in enumerate(row)
        }
        for row in data_rows[:fetch_limit]
    ]
    return keys, records, len(data_rows) > fetch_limit


def _execute_statement(
    target: DbTarget,
    statement: str,
    phase: str,
    command_runner: CommandRunner,
) -> dict[str, Any]:
    if not target.restart_container:
        raise MemgraphCliError("memgraph_cli target requires restart_container")
    args = [
        "docker",
        "exec",
        "-i",
        target.restart_container,
        "mgconsole",
        "--output_format=csv",
        "--no_history",
    ]
    input_text = statement.rstrip().rstrip(";") + ";\n"
    started = perf_counter()
    completed = command_runner(args, input_text, target.query_timeout_sec)
    if completed.returncode != 0:
        error = (completed.stderr or completed.stdout).strip()
        raise MemgraphCliError(error or f"mgconsole exited {completed.returncode}")
    keys, records, truncated = _parse_csv_output(
        completed.stdout, target.fetch_limit
    )
    return {
        "phase": phase,
        "statement": statement,
        "elapsed_ms": round((perf_counter() - started) * 1000.0, 3),
        "keys": keys,
        "records": records,
        "records_truncated": truncated,
        "counters": {},
        "query_type": "cypher(memgraph-mgconsole)",
        "query_timeout_sec": target.query_timeout_sec,
    }


def _execute_query_block(
    target: DbTarget,
    queries: Iterable[str],
    phase: str,
    command_runner: CommandRunner,
) -> List[dict[str, Any]]:
    phases: List[dict[str, Any]] = []
    for raw in queries:
        for statement in split_cypher_statements(raw):
            phases.append(
                _execute_statement(target, statement, phase, command_runner)
            )
    return phases


def probe_memgraph_cli(
    target: DbTarget,
    *,
    command_runner: CommandRunner | None = None,
) -> bool:
    runner = command_runner or _default_command_runner
    try:
        _execute_statement(target, "RETURN 1 AS ready", "probe", runner)
    except Exception:
        return False
    return True


def execute_case_against_target_memgraph_cli(
    case: Dict[str, Any],
    target: DbTarget,
    run_cleanup: bool = True,
    *,
    command_runner: CommandRunner | None = None,
) -> Dict[str, Any]:
    runner = command_runner or _default_command_runner
    started = perf_counter()
    status = "passed"
    error: str | None = None
    phases: List[dict[str, Any]] = []
    try:
        if target.pre_case_queries:
            phases.extend(
                _execute_query_block(
                    target, target.pre_case_queries, "pre_case", runner
                )
            )
        phases.extend(
            _execute_query_block(
                target, case.get("setup_queries", []), "setup", runner
            )
        )
        phases.extend(
            _execute_query_block(
                target, [case.get("main_query", "")], "main", runner
            )
        )
    except Exception as exc:
        status = "failed"
        error = str(exc)
    finally:
        if run_cleanup:
            try:
                phases.extend(
                    _execute_query_block(
                        target,
                        case.get("cleanup_queries", []),
                        "cleanup",
                        runner,
                    )
                )
                if target.post_case_queries:
                    phases.extend(
                        _execute_query_block(
                            target,
                            target.post_case_queries,
                            "post_case",
                            runner,
                        )
                    )
            except Exception as cleanup_exc:
                if status == "passed":
                    status = "cleanup_failed"
                    error = str(cleanup_exc)
    return {
        "query_id": case.get("query_id"),
        "case_id": case.get("case_id"),
        "target": target.name,
        "status": status,
        "error": error,
        "elapsed_ms": round((perf_counter() - started) * 1000.0, 3),
        "main_query": case.get("main_query"),
        "query_timeout_sec": target.query_timeout_sec,
        "restart_count": 0,
        "phases": phases,
    }
