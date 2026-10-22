from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from time import perf_counter
from typing import Any, Dict, Iterable, List

from src.stage4.executor import DbTarget, split_cypher_statements


def _base_url(target: DbTarget) -> str:
    if target.uri.startswith(("http://", "https://")):
        return target.uri.rstrip("/")
    return f"http://{target.host or '127.0.0.1'}:{target.port or 7474}"


def _authorization(target: DbTarget) -> str:
    auth = target.auth()
    if not auth:
        return ""
    user, password = auth
    encoded = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {encoded}"


def _request_statement(target: DbTarget, statement: str) -> Dict[str, Any]:
    body = json.dumps(
        {"statements": [{"statement": statement}]}, ensure_ascii=False
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    authorization = _authorization(target)
    if authorization:
        headers["Authorization"] = authorization
    request = urllib.request.Request(
        f"{_base_url(target)}/db/data/transaction/commit",
        data=body,
        method="POST",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(
            request, timeout=max(float(target.query_timeout_sec or 10.0), 1.0)
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(details or str(error)) from error
    if not isinstance(payload, dict):
        raise RuntimeError("Neo4j HTTP response root is not an object")
    errors = payload.get("errors")
    if isinstance(errors, list) and errors:
        raise RuntimeError(json.dumps(errors[0], ensure_ascii=False, sort_keys=True))
    return payload


def _execute_statement(
    target: DbTarget,
    statement: str,
    phase: str,
) -> Dict[str, Any]:
    started = perf_counter()
    payload = _request_statement(target, statement)
    results = payload.get("results")
    result = results[0] if isinstance(results, list) and results else {}
    columns = result.get("columns") if isinstance(result, dict) else []
    raw_data = result.get("data") if isinstance(result, dict) else []
    keys = [str(column) for column in columns] if isinstance(columns, list) else []
    rows: list[dict[str, Any]] = []
    if isinstance(raw_data, list):
        for item in raw_data:
            raw_row = item.get("row") if isinstance(item, dict) else None
            if isinstance(raw_row, list):
                rows.append(dict(zip(keys, raw_row)))
    return {
        "phase": phase,
        "statement": statement,
        "elapsed_ms": round((perf_counter() - started) * 1000.0, 3),
        "keys": keys,
        "records": rows[: target.fetch_limit],
        "records_truncated": len(rows) > target.fetch_limit,
        "counters": {},
        "query_type": "cypher(neo4j-transaction-http)",
        "query_timeout_sec": target.query_timeout_sec,
    }


def _execute_query_block(
    target: DbTarget,
    queries: Iterable[str],
    phase: str,
) -> List[Dict[str, Any]]:
    phases: List[Dict[str, Any]] = []
    for raw in queries:
        for statement in split_cypher_statements(raw):
            phases.append(_execute_statement(target, statement, phase))
    return phases


def execute_case_against_target_neo4j_http(
    case: Dict[str, Any],
    target: DbTarget,
    run_cleanup: bool = True,
) -> Dict[str, Any]:
    started = perf_counter()
    status = "passed"
    error: str | None = None
    phases: List[Dict[str, Any]] = []
    try:
        if target.pre_case_queries:
            phases.extend(_execute_query_block(target, target.pre_case_queries, "pre_case"))
        phases.extend(
            _execute_query_block(target, case.get("setup_queries", []), "setup")
        )
        phases.extend(
            _execute_query_block(target, [case.get("main_query", "")], "main")
        )
    except Exception as execution_error:
        status = "failed"
        error = str(execution_error)
    finally:
        if run_cleanup:
            try:
                phases.extend(
                    _execute_query_block(
                        target, case.get("cleanup_queries", []), "cleanup"
                    )
                )
                if target.post_case_queries:
                    phases.extend(
                        _execute_query_block(
                            target, target.post_case_queries, "post_case"
                        )
                    )
            except Exception as cleanup_error:
                if status == "passed":
                    status = "cleanup_failed"
                    error = str(cleanup_error)
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
