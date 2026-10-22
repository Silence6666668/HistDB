from __future__ import annotations

import os
import multiprocessing as mp
import subprocess
import time
import warnings
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Callable, Dict, Iterable, List

from neo4j import GraphDatabase, Query

warnings.filterwarnings(
    "ignore",
    message=r"Received notification from DBMS server:.*",
)


@dataclass
class DbTarget:
    name: str
    engine: str = "bolt"
    uri: str = ""
    host: str | None = None
    port: int | None = None
    user: str | None = None
    password_env: str | None = None
    password: str | None = None
    database: str | None = None
    graph_name: str | None = None
    pre_case_queries: List[str] | None = None
    post_case_queries: List[str] | None = None
    pre_case_sql: List[str] | None = None
    post_case_sql: List[str] | None = None
    fetch_limit: int = 20
    query_timeout_sec: float | None = None
    hard_timeout_sec: float | None = None
    worker_startup_timeout_sec: float = 120.0
    restart_container: str | None = None
    restart_on_failure: bool = False
    restart_wait_sec: float = 15.0
    restart_max_retries: int = 1
    reset_schema_before_case: bool = False
    arcadedb_auth_mode: str = "bearer"
    arcadedb_create_mode: str = "server_command"
    arcadedb_query_language: str = "opencypher"
    arcadedb_serializer: str = "studio"

    def auth(self) -> Any:
        if self.user:
            password = self.password
            if password is None and self.password_env:
                password = os.getenv(self.password_env)
            if password is None:
                raise EnvironmentError(
                    f"Target {self.name} requires password env {self.password_env or '<missing>'}."
                )
            return (self.user, password)
        return None


def load_db_targets(cfg: Dict[str, Any], selected_names: Iterable[str] | None = None) -> List[DbTarget]:
    selected = {name.strip() for name in (selected_names or []) if str(name).strip()}
    out: List[DbTarget] = []
    for item in cfg.get("targets", []) or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        if selected and name not in selected:
            continue
        if item.get("enabled", True) is False:
            continue
        query_timeout_value = item.get("query_timeout_sec")
        out.append(
            DbTarget(
                name=name,
                engine=str(item.get("engine") or "bolt").strip().lower(),
                uri=str(item.get("uri") or ""),
                host=(str(item.get("host")).strip() if item.get("host") not in (None, "") else None),
                port=(int(item.get("port")) if item.get("port") not in (None, "") else None),
                user=(str(item.get("user")).strip() if item.get("user") not in (None, "") else None),
                password_env=(str(item.get("password_env")).strip() if item.get("password_env") not in (None, "") else None),
                password=(str(item.get("password")).strip() if item.get("password") not in (None, "") else None),
                database=(str(item.get("database")).strip() if item.get("database") not in (None, "") else None),
                graph_name=(str(item.get("graph_name")).strip() if item.get("graph_name") not in (None, "") else None),
                pre_case_queries=[str(q) for q in item.get("pre_case_queries", []) if str(q).strip()],
                post_case_queries=[str(q) for q in item.get("post_case_queries", []) if str(q).strip()],
                pre_case_sql=[str(q) for q in item.get("pre_case_sql", []) if str(q).strip()],
                post_case_sql=[str(q) for q in item.get("post_case_sql", []) if str(q).strip()],
                fetch_limit=int(item.get("fetch_limit", 20)),
                query_timeout_sec=(
                    float(query_timeout_value)
                    if query_timeout_value not in (None, "")
                    else None
                ),
                hard_timeout_sec=(
                    float(item.get("hard_timeout_sec"))
                    if item.get("hard_timeout_sec") not in (None, "")
                    else None
                ),
                worker_startup_timeout_sec=float(
                    item.get("worker_startup_timeout_sec", 120.0)
                ),
                restart_container=(
                    str(item.get("restart_container")).strip()
                    if item.get("restart_container") not in (None, "")
                    else None
                ),
                restart_on_failure=bool(item.get("restart_on_failure", False)),
                restart_wait_sec=float(item.get("restart_wait_sec", 15.0)),
                restart_max_retries=int(item.get("restart_max_retries", 1)),
                reset_schema_before_case=bool(item.get("reset_schema_before_case", False)),
                arcadedb_auth_mode=str(item.get("arcadedb_auth_mode", "bearer")),
                arcadedb_create_mode=str(item.get("arcadedb_create_mode", "server_command")),
                arcadedb_query_language=str(item.get("arcadedb_query_language", "opencypher")),
                arcadedb_serializer=str(item.get("arcadedb_serializer", "studio")),
            )
        )
    return out


def split_cypher_statements(text: str) -> List[str]:
    statements: List[str] = []
    buf: List[str] = []
    in_single = False
    in_double = False
    escape = False
    for ch in text or "":
        buf.append(ch)
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            continue
        if ch == ";" and not in_single and not in_double:
            stmt = "".join(buf[:-1]).strip()
            if stmt:
                statements.append(stmt)
            buf = []
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_sanitize_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _sanitize_value(v) for k, v in value.items()}
    return str(value)


def _serialize_counters(counters: Any) -> Dict[str, Any]:
    public_attrs = [
        "constraints_added",
        "constraints_removed",
        "contains_system_updates",
        "contains_updates",
        "indexes_added",
        "indexes_removed",
        "labels_added",
        "labels_removed",
        "nodes_created",
        "nodes_deleted",
        "notifications",
        "properties_set",
        "relationships_created",
        "relationships_deleted",
        "system_updates",
    ]
    out: Dict[str, Any] = {}
    for name in public_attrs:
        if hasattr(counters, name):
            out[name] = _sanitize_value(getattr(counters, name))
    return out


def _build_query(statement: str, target: DbTarget) -> Query | str:
    if target.query_timeout_sec is None:
        return statement
    return Query(statement, timeout=target.query_timeout_sec)


def _open_driver(target: DbTarget) -> Any:
    return GraphDatabase.driver(
        target.uri,
        auth=target.auth(),
        warn_notification_severity="OFF",
    )


def _quote_neo4j_identifier(name: str) -> str:
    return "`" + str(name).replace("`", "``") + "`"


def build_neo4j_schema_drop_queries(
    constraint_names: Iterable[str],
    indexes: Iterable[Dict[str, Any]],
) -> List[str]:
    queries = [
        f"DROP CONSTRAINT {_quote_neo4j_identifier(name)} IF EXISTS"
        for name in constraint_names
        if str(name).strip()
    ]
    for index in indexes:
        name = str(index.get("name") or "").strip()
        index_type = str(index.get("type") or "").strip().upper()
        if name and index_type != "LOOKUP":
            queries.append(f"DROP INDEX {_quote_neo4j_identifier(name)} IF EXISTS")
    return queries


def _read_neo4j_schema_records(driver: Any, target: DbTarget, statement: str) -> List[Dict[str, Any]]:
    session_kwargs = {"database": target.database} if target.database else {}
    with driver.session(**session_kwargs) as session:
        result = session.run(_build_query(statement, target))
        records = [_sanitize_value(record.data()) for record in result]
        result.consume()
    return records


def reset_neo4j_schema(driver: Any, target: DbTarget) -> List[Dict[str, Any]]:
    phases: List[Dict[str, Any]] = []
    constraints = _read_neo4j_schema_records(
        driver,
        target,
        "SHOW CONSTRAINTS YIELD name RETURN name",
    )
    constraint_queries = build_neo4j_schema_drop_queries(
        [str(item.get("name") or "") for item in constraints],
        [],
    )
    phases.extend(execute_query_block(driver, target, constraint_queries, "pre_case_schema"))

    indexes = _read_neo4j_schema_records(
        driver,
        target,
        "SHOW INDEXES YIELD name, type RETURN name, type",
    )
    index_queries = build_neo4j_schema_drop_queries([], indexes)
    phases.extend(execute_query_block(driver, target, index_queries, "pre_case_schema"))
    return phases


def _should_attempt_restart(target: DbTarget, exc: Exception) -> bool:
    if not target.restart_on_failure or not target.restart_container:
        return False
    text = str(exc).lower()
    markers = [
        "defunct connection",
        "couldn't connect",
        "failed to read from defunct connection",
        "connection refused",
        "service unavailable",
        "connection reset",
        "failed to establish connection",
    ]
    return any(marker in text for marker in markers)


def _wait_until_ready(
    probe: Callable[[], bool],
    *,
    timeout_sec: float,
    interval_sec: float = 1.0,
) -> bool:
    deadline = time.monotonic() + max(timeout_sec, 0.0)
    while True:
        try:
            if probe():
                return True
        except Exception:
            pass
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(max(interval_sec, 0.0), remaining))


def _target_is_ready(target: DbTarget) -> bool:
    driver = GraphDatabase.driver(
        target.uri,
        auth=target.auth(),
        warn_notification_severity="OFF",
        connection_timeout=1.0,
        connection_acquisition_timeout=1.0,
    )
    try:
        driver.verify_connectivity()
        return True
    finally:
        driver.close()


def _restart_target_container(target: DbTarget) -> None:
    if not target.restart_container:
        raise RuntimeError(f"Target {target.name} does not define restart_container.")
    subprocess.run(
        ["docker", "restart", target.restart_container],
        check=True,
        capture_output=True,
        text=True,
    )
    if not _wait_until_ready(
        lambda: _target_is_ready(target),
        timeout_sec=target.restart_wait_sec,
    ):
        raise TimeoutError(
            f"Container {target.restart_container} restarted, but the DBMS did not become ready in time."
        )


def execute_query_block(driver: Any, target: DbTarget, queries: Iterable[str], phase: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for raw in queries:
        for statement in split_cypher_statements(raw):
            started = perf_counter()
            session_kwargs = {"database": target.database} if target.database else {}
            with driver.session(**session_kwargs) as session:
                result = session.run(_build_query(statement, target))
                records = result.fetch(target.fetch_limit)
                summary = result.consume()
            out.append(
                {
                    "phase": phase,
                    "statement": statement,
                    "elapsed_ms": round((perf_counter() - started) * 1000.0, 3),
                    "keys": list(result.keys()),
                    "records": [_sanitize_value(record.data()) for record in records],
                    "records_truncated": len(records) >= target.fetch_limit,
                    "counters": _serialize_counters(summary.counters),
                    "query_type": summary.query_type,
                    "query_timeout_sec": target.query_timeout_sec,
                }
            )
    return out


def execute_case_against_target(
    case: Dict[str, Any],
    target: DbTarget,
    run_cleanup: bool = True,
) -> Dict[str, Any]:
    started = perf_counter()
    status = "passed"
    error: str | None = None
    phases: List[Dict[str, Any]] = []
    restart_count = 0
    try:
        while True:
            driver = _open_driver(target)
            try:
                if target.reset_schema_before_case:
                    phases.extend(reset_neo4j_schema(driver, target))
                if target.pre_case_queries:
                    phases.extend(execute_query_block(driver, target, target.pre_case_queries, "pre_case"))
                phases.extend(execute_query_block(driver, target, case.get("setup_queries", []), "setup"))
                phases.extend(execute_query_block(driver, target, [case.get("main_query", "")], "main"))
                break
            except Exception as exc:
                driver.close()
                if restart_count < target.restart_max_retries and _should_attempt_restart(target, exc):
                    restart_count += 1
                    _restart_target_container(target)
                    continue
                status = "failed"
                error = str(exc)
                break
            finally:
                try:
                    driver.close()
                except Exception:
                    pass
    finally:
        if run_cleanup:
            try:
                driver = _open_driver(target)
                phases.extend(execute_query_block(driver, target, case.get("cleanup_queries", []), "cleanup"))
                if target.post_case_queries:
                    phases.extend(execute_query_block(driver, target, target.post_case_queries, "post_case"))
            except Exception as cleanup_exc:
                if status == "passed":
                    status = "cleanup_failed"
                    error = str(cleanup_exc)
            finally:
                try:
                    driver.close()
                except Exception:
                    pass
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


def _execute_case_worker(
    queue: Any,
    ready: Any,
    case: Dict[str, Any],
    target: DbTarget,
    run_cleanup: bool,
) -> None:
    ready.set()
    try:
        result = execute_case_against_target(case, target, run_cleanup=run_cleanup)
        queue.put({"ok": True, "result": result})
    except Exception as exc:  # pragma: no cover - defensive worker boundary
        queue.put({"ok": False, "error": str(exc)})


def execute_case_against_target_with_hard_timeout(
    case: Dict[str, Any],
    target: DbTarget,
    run_cleanup: bool = True,
) -> Dict[str, Any]:
    if not target.hard_timeout_sec or target.hard_timeout_sec <= 0:
        return execute_case_against_target(case, target, run_cleanup=run_cleanup)

    started = perf_counter()
    ctx = mp.get_context("spawn")
    queue = ctx.Queue()
    ready = ctx.Event()
    proc = ctx.Process(
        target=_execute_case_worker,
        args=(queue, ready, case, target, run_cleanup),
    )
    proc.start()
    if not ready.wait(target.worker_startup_timeout_sec):
        if proc.is_alive():
            proc.terminate()
            proc.join(5.0)
        return {
            "query_id": case.get("query_id"),
            "case_id": case.get("case_id"),
            "target": target.name,
            "status": "failed",
            "error": (
                "Worker startup timeout exceeded after "
                f"{target.worker_startup_timeout_sec} seconds"
            ),
            "elapsed_ms": round((perf_counter() - started) * 1000.0, 3),
            "main_query": case.get("main_query"),
            "query_timeout_sec": target.query_timeout_sec,
            "hard_timeout_sec": target.hard_timeout_sec,
            "worker_startup_timeout_sec": target.worker_startup_timeout_sec,
            "restart_count": 0,
            "phases": [],
        }
    proc.join(target.hard_timeout_sec)

    if proc.is_alive():
        proc.terminate()
        proc.join(5.0)
        restart_count = 0
        if target.restart_on_failure and target.restart_container:
            restart_count = 1
            try:
                _restart_target_container(target)
            except Exception:
                pass
        return {
            "query_id": case.get("query_id"),
            "case_id": case.get("case_id"),
            "target": target.name,
            "status": "failed",
            "error": f"Hard timeout exceeded after {target.hard_timeout_sec} seconds",
            "elapsed_ms": round((perf_counter() - started) * 1000.0, 3),
            "main_query": case.get("main_query"),
            "query_timeout_sec": target.query_timeout_sec,
            "hard_timeout_sec": target.hard_timeout_sec,
            "restart_count": restart_count,
            "phases": [],
        }

    payload = queue.get() if not queue.empty() else None
    if not payload:
        return {
            "query_id": case.get("query_id"),
            "case_id": case.get("case_id"),
            "target": target.name,
            "status": "failed",
            "error": "Worker exited without returning a result",
            "elapsed_ms": round((perf_counter() - started) * 1000.0, 3),
            "main_query": case.get("main_query"),
            "query_timeout_sec": target.query_timeout_sec,
            "hard_timeout_sec": target.hard_timeout_sec,
            "restart_count": 0,
            "phases": [],
        }
    if not payload.get("ok"):
        return {
            "query_id": case.get("query_id"),
            "case_id": case.get("case_id"),
            "target": target.name,
            "status": "failed",
            "error": payload.get("error") or "Worker execution failed",
            "elapsed_ms": round((perf_counter() - started) * 1000.0, 3),
            "main_query": case.get("main_query"),
            "query_timeout_sec": target.query_timeout_sec,
            "hard_timeout_sec": target.hard_timeout_sec,
            "restart_count": 0,
            "phases": [],
        }
    result = payload["result"]
    result["hard_timeout_sec"] = target.hard_timeout_sec
    return result
