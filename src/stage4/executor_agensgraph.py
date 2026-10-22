from __future__ import annotations

from time import perf_counter
from typing import Any, Dict, Iterable, List

import psycopg

from src.stage4.executor import DbTarget, split_cypher_statements


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_sanitize_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _sanitize_value(v) for k, v in value.items()}
    return str(value)


def _sql_literal(value: str) -> str:
    return value.replace("'", "''")


def _dedupe_keys(keys: List[str]) -> List[str]:
    seen: Dict[str, int] = {}
    out: List[str] = []
    for key in keys:
        count = seen.get(key, 0) + 1
        seen[key] = count
        if count == 1:
            out.append(key)
        else:
            out.append(f"{key}__dup{count}")
    return out


def _connect(target: DbTarget) -> psycopg.Connection:
    password = target.password
    if password is None and target.password_env:
        import os

        password = os.getenv(target.password_env)
    conn = psycopg.connect(
        host=target.host or "localhost",
        port=target.port or 5432,
        user=target.user,
        password=password,
        dbname=target.database or "postgres",
        autocommit=True,
    )
    return conn


def _set_graph_path(conn: psycopg.Connection, target: DbTarget) -> None:
    graph_name = target.graph_name or "fuzz_graph"
    with conn.cursor() as cur:
        cur.execute(f"SET graph_path = '{_sql_literal(graph_name)}'")


def _run_sql_queries(
    conn: psycopg.Connection,
    target: DbTarget,
    queries: Iterable[str],
    phase: str,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for raw in queries:
        for statement in split_cypher_statements(raw):
            started = perf_counter()
            with conn.cursor() as cur:
                cur.execute(statement)
                records: List[Dict[str, Any]] = []
                keys: List[str] = []
                if cur.description:
                    keys = _dedupe_keys([str(col.name) for col in cur.description])
                    rows = cur.fetchmany(target.fetch_limit)
                    records = [
                        {keys[idx]: _sanitize_value(value) for idx, value in enumerate(row)}
                        for row in rows
                    ]
            out.append(
                {
                    "phase": phase,
                    "statement": statement,
                    "elapsed_ms": round((perf_counter() - started) * 1000.0, 3),
                    "keys": keys,
                    "records": records,
                    "records_truncated": len(records) >= target.fetch_limit,
                    "counters": {},
                    "query_type": "sql",
                    "query_timeout_sec": target.query_timeout_sec,
                }
            )
    return out


def execute_cypher_block_agensgraph(
    conn: psycopg.Connection,
    target: DbTarget,
    queries: Iterable[str],
    phase: str,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for raw in queries:
        for statement in split_cypher_statements(raw):
            started = perf_counter()
            with conn.cursor() as cur:
                if target.query_timeout_sec is not None:
                    cur.execute(f"SET statement_timeout = {int(target.query_timeout_sec * 1000)}")
                cur.execute(statement)
                keys = _dedupe_keys([str(col.name) for col in cur.description]) if cur.description else []
                rows = cur.fetchmany(target.fetch_limit) if cur.description else []
                records = [
                    {keys[idx]: _sanitize_value(value) for idx, value in enumerate(row)}
                    for row in rows
                ]
            out.append(
                {
                    "phase": phase,
                    "statement": statement,
                    "elapsed_ms": round((perf_counter() - started) * 1000.0, 3),
                    "keys": keys,
                    "records": records,
                    "records_truncated": len(records) >= target.fetch_limit,
                    "counters": {},
                    "query_type": "cypher(agensgraph)",
                    "query_timeout_sec": target.query_timeout_sec,
                }
            )
    return out


def execute_case_against_target_agensgraph(
    case: Dict[str, Any],
    target: DbTarget,
    run_cleanup: bool = True,
) -> Dict[str, Any]:
    conn = _connect(target)
    started = perf_counter()
    status = "passed"
    error: str | None = None
    phases: List[Dict[str, Any]] = []
    try:
        if target.pre_case_sql:
            phases.extend(_run_sql_queries(conn, target, target.pre_case_sql, "pre_case_sql"))
        _set_graph_path(conn, target)
        if target.pre_case_queries:
            phases.extend(execute_cypher_block_agensgraph(conn, target, target.pre_case_queries, "pre_case"))
        phases.extend(execute_cypher_block_agensgraph(conn, target, case.get("setup_queries", []), "setup"))
        phases.extend(execute_cypher_block_agensgraph(conn, target, [case.get("main_query", "")], "main"))
    except Exception as exc:
        status = "failed"
        error = str(exc)
    finally:
        if run_cleanup:
            try:
                _set_graph_path(conn, target)
                phases.extend(execute_cypher_block_agensgraph(conn, target, case.get("cleanup_queries", []), "cleanup"))
                if target.post_case_queries:
                    phases.extend(execute_cypher_block_agensgraph(conn, target, target.post_case_queries, "post_case"))
                if target.post_case_sql:
                    phases.extend(_run_sql_queries(conn, target, target.post_case_sql, "post_case_sql"))
            except Exception as cleanup_exc:
                if status == "passed":
                    status = "cleanup_failed"
                    error = str(cleanup_exc)
        conn.close()
    return {
        "query_id": case.get("query_id"),
        "case_id": case.get("case_id"),
        "target": target.name,
        "status": status,
        "error": error,
        "elapsed_ms": round((perf_counter() - started) * 1000.0, 3),
        "main_query": case.get("main_query"),
        "query_timeout_sec": target.query_timeout_sec,
        "phases": phases,
    }
