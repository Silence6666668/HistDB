from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from time import perf_counter
from typing import Any, Dict, Iterable, List

from src.stage4.executor import DbTarget, split_cypher_statements


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_sanitize_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _sanitize_value(v) for k, v in value.items()}
    return str(value)


def _base_url(target: DbTarget) -> str:
    host = target.host or "localhost"
    port = target.port or 2480
    return f"http://{host}:{port}"


def _login(target: DbTarget) -> str:
    if not target.user:
        raise EnvironmentError(f"Target {target.name} requires a user.")
    password = target.password
    if password is None and target.password_env:
        import os

        password = os.getenv(target.password_env)
    if password is None:
        raise EnvironmentError(
            f"Target {target.name} requires password env {target.password_env or '<missing>'}."
        )
    basic = base64.b64encode(f"{target.user}:{password}".encode("ascii")).decode("ascii")
    if target.arcadedb_auth_mode == "basic":
        req = urllib.request.Request(
            f"{_base_url(target)}/api/v1/databases",
            method="GET",
            headers={"Authorization": f"Basic {basic}"},
        )
        with urllib.request.urlopen(
            req, timeout=max(target.query_timeout_sec or 10.0, 10.0)
        ) as response:
            response.read()
        return basic
    if target.arcadedb_auth_mode != "bearer":
        raise ValueError(
            f"Unsupported ArcadeDB auth mode: {target.arcadedb_auth_mode}"
        )
    req = urllib.request.Request(
        f"{_base_url(target)}/api/v1/login",
        method="POST",
        headers={"Authorization": f"Basic {basic}"},
    )
    with urllib.request.urlopen(req, timeout=max(target.query_timeout_sec or 10.0, 10.0)) as response:
        payload = json.loads(response.read().decode("utf-8"))
    token = str(payload.get("token") or "").strip()
    if not token:
        raise RuntimeError(f"ArcadeDB login succeeded but no token was returned for target {target.name}.")
    return token


def _request_json(
    target: DbTarget,
    token: str,
    method: str,
    path: str,
    payload: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    body = None
    auth_scheme = "Basic" if target.arcadedb_auth_mode == "basic" else "Bearer"
    headers = {"Authorization": f"{auth_scheme} {token}"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        f"{_base_url(target)}{path}",
        data=body,
        method=method,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=max(target.query_timeout_sec or 10.0, 10.0)) as response:
            text = response.read().decode("utf-8")
            return json.loads(text) if text.strip() else {}
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(details or str(exc)) from exc


def _extract_result_rows(data: Dict[str, Any]) -> tuple[list[Any], dict[str, Any]]:
    result = data.get("result")
    metadata: dict[str, Any] = {}

    if isinstance(result, list):
        return result, metadata

    if isinstance(result, dict):
        records = result.get("records")
        if isinstance(records, list):
            metadata = {
                "result_vertices": _sanitize_value(result.get("vertices", [])),
                "result_edges": _sanitize_value(result.get("edges", [])),
            }
            return records, metadata

    return [], metadata


def _execute_statement(
    target: DbTarget,
    token: str,
    database: str,
    statement: str,
    phase: str,
) -> Dict[str, Any]:
    started = perf_counter()
    payload = {
        "language": target.arcadedb_query_language,
        "command": statement,
        "limit": target.fetch_limit,
        "profileExecution": "basic",
        "serializer": target.arcadedb_serializer,
    }
    data = _request_json(target, token, "POST", f"/api/v1/command/{database}", payload)
    result_rows, metadata = _extract_result_rows(data)
    records = [_sanitize_value(row) for row in result_rows[: target.fetch_limit]]
    keys = list(records[0].keys()) if records and isinstance(records[0], dict) else []
    return {
        "phase": phase,
        "statement": statement,
        "elapsed_ms": round((perf_counter() - started) * 1000.0, 3),
        "keys": keys,
        "records": records,
        "records_truncated": len(result_rows) > target.fetch_limit,
        "counters": metadata,
        "query_type": (
            f"{target.arcadedb_query_language}"
            f"(arcadedb-{target.arcadedb_serializer}-http)"
        ),
        "query_timeout_sec": target.query_timeout_sec,
    }


def _execute_query_block(
    target: DbTarget,
    token: str,
    database: str,
    queries: Iterable[str],
    phase: str,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for raw in queries:
        for statement in split_cypher_statements(raw):
            out.append(_execute_statement(target, token, database, statement, phase))
    return out


def execute_case_against_target_arcadedb(
    case: Dict[str, Any],
    target: DbTarget,
    run_cleanup: bool = True,
) -> Dict[str, Any]:
    started = perf_counter()
    status = "passed"
    error: str | None = None
    phases: List[Dict[str, Any]] = []
    database = target.database or "arcade"
    try:
        token = _login(target)
        if target.pre_case_queries:
            phases.extend(_execute_query_block(target, token, database, target.pre_case_queries, "pre_case"))
        phases.extend(_execute_query_block(target, token, database, case.get("setup_queries", []), "setup"))
        phases.extend(_execute_query_block(target, token, database, [case.get("main_query", "")], "main"))
    except Exception as exc:
        status = "failed"
        error = str(exc)
    finally:
        if run_cleanup:
            try:
                phases.extend(_execute_query_block(target, token, database, case.get("cleanup_queries", []), "cleanup"))
                if target.post_case_queries:
                    phases.extend(_execute_query_block(target, token, database, target.post_case_queries, "post_case"))
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
        "phases": phases,
    }
