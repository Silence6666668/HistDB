from __future__ import annotations
import re
from time import perf_counter
from typing import Any, Dict, Iterable, List

import psycopg

from src.stage4.executor import DbTarget, _restart_target_container, split_cypher_statements


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


def _pick_dollar_quote(statement: str) -> str:
    candidates = ["$$", "$cypher$", "$age$", "$query$"]
    for token in candidates:
        if token not in statement:
            return token
    idx = 0
    while True:
        token = f"$q{idx}$"
        if token not in statement:
            return token
        idx += 1


def _iter_top_level_words(text: str) -> List[tuple[str, int, int]]:
    words: List[tuple[str, int, int]] = []
    depth = 0
    in_single = False
    in_double = False
    escape = False
    idx = 0
    while idx < len(text):
        ch = text[idx]
        if escape:
            escape = False
            idx += 1
            continue
        if ch == "\\":
            escape = True
            idx += 1
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            idx += 1
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            idx += 1
            continue
        if in_single or in_double:
            idx += 1
            continue
        if ch in "([{":
            depth += 1
            idx += 1
            continue
        if ch in ")]}":
            depth = max(0, depth - 1)
            idx += 1
            continue
        if depth == 0 and (ch.isalpha() or ch == "_"):
            start = idx
            idx += 1
            while idx < len(text) and (text[idx].isalnum() or text[idx] == "_"):
                idx += 1
            words.append((text[start:idx].lower(), start, idx))
            continue
        idx += 1
    return words


def _extract_return_items(statement: str) -> List[str]:
    words = _iter_top_level_words(statement)
    return_word: tuple[str, int, int] | None = None
    for word in words:
        if word[0] == "return":
            return_word = word
    if return_word is None:
        return []

    tail = statement[return_word[2] :].strip().rstrip(";")
    tail_words = _iter_top_level_words(tail)
    cut = len(tail)
    for idx, (word, start, _) in enumerate(tail_words):
        if word == "order" and idx + 1 < len(tail_words) and tail_words[idx + 1][0] == "by":
            cut = min(cut, start)
        elif word in {"skip", "limit"}:
            cut = min(cut, start)
    tail = tail[:cut].strip()
    if not tail:
        return []
    items: List[str] = []
    buf: List[str] = []
    depth = 0
    in_single = False
    in_double = False
    escape = False
    for ch in tail:
        if escape:
            buf.append(ch)
            escape = False
            continue
        if ch == "\\":
            buf.append(ch)
            escape = True
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            buf.append(ch)
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            buf.append(ch)
            continue
        if not in_single and not in_double:
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth = max(0, depth - 1)
            elif ch == "," and depth == 0:
                item = "".join(buf).strip()
                if item:
                    items.append(item)
                buf = []
                continue
        buf.append(ch)
    tail_item = "".join(buf).strip()
    if tail_item:
        items.append(tail_item)
    return items


def _item_alias(item: str, index: int) -> str:
    matches = list(re.finditer(r"(?i)\bas\b", item))
    if matches:
        alias = item[matches[-1].end() :].strip()
        if alias:
            return alias
    if item == "*":
        return f"col_{index}"
    candidate = item.split(".")[-1].strip("` ")
    if candidate.replace("_", "").isalnum() and candidate:
        return candidate
    return f"col_{index}"


def _sql_identifier(name: str, index: int) -> str:
    candidate = name.strip()
    if candidate.startswith("`") and candidate.endswith("`") and len(candidate) >= 2:
        candidate = candidate[1:-1]
    elif candidate.startswith('"') and candidate.endswith('"') and len(candidate) >= 2:
        candidate = candidate[1:-1]
    candidate = candidate.strip()
    if not candidate:
        return f"col_{index}"
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", candidate):
        return candidate
    return '"' + candidate.replace('"', '""') + '"'


def _build_age_sql(statement: str, target: DbTarget) -> str:
    graph_name = target.graph_name or "fuzz_graph"
    return_items = _extract_return_items(statement)
    if "*" in return_items:
        raise ValueError(
            "AGE executor does not support queries whose final RETURN clause contains '*' yet."
        )
    if return_items:
        aliases = [_item_alias(item, idx) for idx, item in enumerate(return_items)]
    else:
        aliases = ["result"]
    columns = ", ".join(f"{_sql_identifier(alias, idx)} agtype" for idx, alias in enumerate(aliases))
    cypher_sql = statement.strip().rstrip(";")
    quote = _pick_dollar_quote(cypher_sql)
    return (
        f"SELECT * FROM cypher('{_sql_literal(graph_name)}', "
        f"{quote}{cypher_sql}{quote}) AS ({columns})"
    )


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
    with conn.cursor() as cur:
        cur.execute("LOAD 'age'")
        cur.execute('SET search_path = ag_catalog, "$user", public')
    return conn


def _should_attempt_restart_age(target: DbTarget, exc: Exception) -> bool:
    if not target.restart_on_failure or not target.restart_container:
        return False
    text = str(exc).lower()
    markers = [
        "recovery mode",
        "server closed the connection unexpectedly",
        "terminating connection",
        "connection refused",
        "connection reset",
        "admin shutdown",
        "could not receive data from server",
        "consuming input failed",
        "ssl connection has been closed unexpectedly",
    ]
    return any(marker in text for marker in markers)


def _close_quietly(conn: psycopg.Connection | None) -> None:
    if conn is None:
        return
    try:
        conn.close()
    except Exception:
        pass


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
                    keys = [str(col.name) for col in cur.description]
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


def execute_cypher_block_age(
    conn: psycopg.Connection,
    target: DbTarget,
    queries: Iterable[str],
    phase: str,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for raw in queries:
        for statement in split_cypher_statements(raw):
            started = perf_counter()
            sql = _build_age_sql(statement, target)
            with conn.cursor() as cur:
                if target.query_timeout_sec is not None:
                    cur.execute(f"SET statement_timeout = {int(target.query_timeout_sec * 1000)}")
                cur.execute(sql)
                keys = [str(col.name) for col in cur.description] if cur.description else []
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
                    "query_type": "cypher(age)",
                    "query_timeout_sec": target.query_timeout_sec,
                    "wrapped_sql": sql,
                }
            )
    return out


def execute_case_against_target_age(
    case: Dict[str, Any],
    target: DbTarget,
    run_cleanup: bool = True,
) -> Dict[str, Any]:
    started = perf_counter()
    restart_count = 0
    status = "passed"
    error: str | None = None
    phases: List[Dict[str, Any]] = []

    while True:
        conn: psycopg.Connection | None = None
        attempt_phases: List[Dict[str, Any]] = []
        try:
            conn = _connect(target)
            if target.pre_case_sql:
                attempt_phases.extend(_run_sql_queries(conn, target, target.pre_case_sql, "pre_case_sql"))
            if target.pre_case_queries:
                attempt_phases.extend(execute_cypher_block_age(conn, target, target.pre_case_queries, "pre_case"))
            attempt_phases.extend(execute_cypher_block_age(conn, target, case.get("setup_queries", []), "setup"))
            attempt_phases.extend(execute_cypher_block_age(conn, target, [case.get("main_query", "")], "main"))

            if run_cleanup:
                attempt_phases.extend(execute_cypher_block_age(conn, target, case.get("cleanup_queries", []), "cleanup"))
                if target.post_case_queries:
                    attempt_phases.extend(execute_cypher_block_age(conn, target, target.post_case_queries, "post_case"))
                if target.post_case_sql:
                    attempt_phases.extend(_run_sql_queries(conn, target, target.post_case_sql, "post_case_sql"))

            phases = attempt_phases
            status = "passed"
            error = None
            break
        except Exception as exc:
            _close_quietly(conn)
            if restart_count < target.restart_max_retries and _should_attempt_restart_age(target, exc):
                restart_count += 1
                try:
                    _restart_target_container(target)
                except Exception as restart_exc:
                    phases = attempt_phases
                    status = "failed"
                    error = (
                        f"{exc}; restart failed after AGE connection loss: "
                        f"{restart_exc}"
                    )
                    break
                continue

            phases = attempt_phases
            status = "failed"
            error = str(exc)
            break
        finally:
            _close_quietly(conn)

    return {
        "query_id": case.get("query_id"),
        "case_id": case.get("case_id"),
        "target": target.name,
        "status": status,
        "error": error,
        "elapsed_ms": round((perf_counter() - started) * 1000.0, 3),
        "main_query": case.get("main_query"),
        "query_timeout_sec": target.query_timeout_sec,
        "restart_count": restart_count,
        "phases": phases,
    }
