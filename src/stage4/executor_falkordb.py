from __future__ import annotations

import os
import socket
from time import perf_counter
from typing import Any, Dict, Iterable, List

from src.stage4.executor import DbTarget, _restart_target_container, split_cypher_statements


class FalkorDbError(RuntimeError):
    pass


class _RespConnection:
    def __init__(self, host: str, port: int, timeout_sec: float) -> None:
        self.sock = socket.create_connection((host, port), timeout=timeout_sec)
        self.sock.settimeout(timeout_sec)
        self.file = self.sock.makefile("rb")

    def close(self) -> None:
        try:
            self.file.close()
        finally:
            self.sock.close()

    def execute(self, *parts: str) -> Any:
        payload = self._encode(parts)
        self.sock.sendall(payload)
        return self._read_reply()

    def _encode(self, parts: Iterable[str]) -> bytes:
        parts_list = [str(part) for part in parts]
        out = [f"*{len(parts_list)}\r\n".encode("utf-8")]
        for part in parts_list:
            data = part.encode("utf-8")
            out.append(f"${len(data)}\r\n".encode("utf-8"))
            out.append(data + b"\r\n")
        return b"".join(out)

    def _read_line(self) -> bytes:
        line = self.file.readline()
        if not line:
            raise FalkorDbError("FalkorDB connection closed unexpectedly.")
        if not line.endswith(b"\r\n"):
            raise FalkorDbError(f"Invalid RESP line: {line!r}")
        return line[:-2]

    def _read_reply(self) -> Any:
        prefix = self.file.read(1)
        if not prefix:
            raise FalkorDbError("FalkorDB connection closed unexpectedly.")
        if prefix == b"+":
            return self._read_line().decode("utf-8", errors="replace")
        if prefix == b"-":
            raise FalkorDbError(self._read_line().decode("utf-8", errors="replace"))
        if prefix == b":":
            return int(self._read_line())
        if prefix == b"$":
            length = int(self._read_line())
            if length == -1:
                return None
            data = self.file.read(length)
            trailer = self.file.read(2)
            if trailer != b"\r\n":
                raise FalkorDbError("Invalid bulk string terminator from FalkorDB.")
            return data.decode("utf-8", errors="replace")
        if prefix == b"*":
            length = int(self._read_line())
            if length == -1:
                return None
            return [self._read_reply() for _ in range(length)]
        raise FalkorDbError(f"Unsupported RESP prefix from FalkorDB: {prefix!r}")


def _lookup_name(values: List[str], index: Any, prefix: str) -> str:
    if isinstance(index, int) and 0 <= index < len(values):
        return str(values[index])
    return f"{prefix}_{index}"


def _fetch_schema(conn: _RespConnection, graph_name: str) -> Dict[str, List[str]]:
    schema: Dict[str, List[str]] = {
        "labels": [],
        "relationship_types": [],
        "property_keys": [],
    }
    commands = {
        "labels": "CALL db.labels()",
        "relationship_types": "CALL db.relationshipTypes()",
        "property_keys": "CALL db.propertyKeys()",
    }
    for key, statement in commands.items():
        try:
            reply = conn.execute("GRAPH.QUERY", graph_name, statement, "--compact")
            if not (isinstance(reply, list) and len(reply) >= 2 and isinstance(reply[1], list)):
                continue
            values: List[str] = []
            for row in reply[1]:
                if not (isinstance(row, list) and row):
                    continue
                values.append(str(_sanitize_value(row[0], schema)))
            schema[key] = values
        except Exception:
            schema[key] = []
    return schema


def _decode_property_triplets(values: Any, schema: Dict[str, List[str]]) -> Dict[str, Any]:
    properties: Dict[str, Any] = {}
    if not isinstance(values, list):
        return properties
    for item in values:
        if not (isinstance(item, list) and len(item) == 3):
            continue
        prop_id, type_tag, raw_value = item
        key = _lookup_name(schema.get("property_keys", []), prop_id, "prop")
        if isinstance(type_tag, int):
            properties[key] = _decode_compact_value([type_tag, raw_value], schema)
        else:
            properties[key] = _decode_compact_value(raw_value, schema)
    return properties


def _decode_relationship_value(value: Any, schema: Dict[str, List[str]]) -> Any:
    if not (isinstance(value, list) and len(value) == 5 and isinstance(value[0], int)):
        return None
    _, rel_type_id, src_id, dst_id, props = value
    rel_type = _lookup_name(schema.get("relationship_types", []), rel_type_id, "reltype")
    rel_props = _decode_property_triplets(props, schema)
    out: Dict[str, Any] = {
        "type": rel_type,
        "src": src_id,
        "dst": dst_id,
    }
    if rel_props:
        out["properties"] = rel_props
    return out


def _decode_node_value(value: Any, schema: Dict[str, List[str]]) -> Any:
    if not (isinstance(value, list) and len(value) == 3 and isinstance(value[0], int) and isinstance(value[1], list)):
        return None
    _, label_ids, props = value
    prop_map = _decode_property_triplets(props, schema)
    label_names = [
        _lookup_name(schema.get("labels", []), label_id, "label")
        for label_id in label_ids
        if isinstance(label_id, int)
    ]
    if label_names and "__labels__" not in prop_map:
        prop_map["__labels__"] = label_names
    if not prop_map:
        prop_map = {"__node_id__": value[0]}
    return prop_map


def _decode_path_value(value: Any, schema: Dict[str, List[str]]) -> Any:
    if not (isinstance(value, list) and len(value) == 2 and isinstance(value[0], list) and isinstance(value[1], list)):
        return None
    nodes_raw, rels_raw = value
    if not all(isinstance(node, list) for node in nodes_raw):
        return None
    nodes = [_decode_compact_value(node, schema) for node in nodes_raw]
    rels = [_decode_relationship_value(rel, schema) for rel in rels_raw]
    if any(rel is None for rel in rels):
        return None
    path_parts: List[Any] = []
    for idx, node in enumerate(nodes):
        path_parts.append(node)
        if idx < len(rels):
            rel_obj = rels[idx]
            assert isinstance(rel_obj, dict)
            rel_props = rel_obj.get("properties") or {}
            rel_type = rel_obj.get("type")
            if rel_props:
                path_parts.append({"type": rel_type, "properties": rel_props})
            else:
                path_parts.append(rel_type)
    return path_parts


def _decode_compact_value(value: Any, schema: Dict[str, List[str]] | None = None) -> Any:
    schema = schema or {"labels": [], "relationship_types": [], "property_keys": []}
    node_value = _decode_node_value(value, schema)
    if node_value is not None:
        return node_value
    rel_value = _decode_relationship_value(value, schema)
    if rel_value is not None:
        return rel_value
    path_value = _decode_path_value(value, schema)
    if path_value is not None:
        return path_value
    if isinstance(value, list) and len(value) == 2 and isinstance(value[0], int):
        type_tag = value[0]
        payload = value[1]
        if type_tag == 4 and isinstance(payload, str):
            lowered = payload.lower()
            if lowered == "true":
                return True
            if lowered == "false":
                return False
        if type_tag == 5 and isinstance(payload, str):
            try:
                return int(payload)
            except ValueError:
                try:
                    return float(payload)
                except ValueError:
                    pass
        if type_tag == 10 and isinstance(payload, list):
            if len(payload) % 2 == 0 and all(isinstance(payload[idx], str) for idx in range(0, len(payload), 2)):
                return {
                    str(payload[idx]): _decode_compact_value(payload[idx + 1], schema)
                    for idx in range(0, len(payload), 2)
                }
        return _decode_compact_value(payload, schema)
    if isinstance(value, list):
        return [_decode_compact_value(v, schema) for v in value]
    if isinstance(value, dict):
        return {str(k): _decode_compact_value(v, schema) for k, v in value.items()}
    return value


def _sanitize_decoded_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_sanitize_decoded_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _sanitize_decoded_value(v) for k, v in value.items()}
    return str(value)


def _sanitize_value(value: Any, schema: Dict[str, List[str]] | None = None) -> Any:
    decoded = _decode_compact_value(value, schema)
    return _sanitize_decoded_value(decoded)


def _target_password(target: DbTarget) -> str | None:
    if target.password is not None:
        return target.password
    if target.password_env:
        return os.getenv(target.password_env)
    return None


def _connect(target: DbTarget) -> _RespConnection:
    host = target.host or "localhost"
    port = target.port or 6379
    timeout_sec = max(target.query_timeout_sec or 10.0, 10.0)
    conn = _RespConnection(host, port, timeout_sec)
    password = _target_password(target)
    if password:
        if target.user:
            conn.execute("AUTH", target.user, password)
        else:
            conn.execute("AUTH", password)
    return conn


def _parse_stats(stats: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if not isinstance(stats, list):
        return out
    for item in stats:
        if not isinstance(item, str) or ":" not in item:
            continue
        key, raw_value = item.split(":", 1)
        key = key.strip().lower().replace(" ", "_")
        value_text = raw_value.strip()
        try:
            value: Any = int(value_text)
        except ValueError:
            value = value_text
        out[key] = value
    return out


def _looks_like_stats_row(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(item, str) and ":" in item for item in value)


def _normalize_header_item(item: Any) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, list) and item:
        return str(item[-1])
    return str(item)


def _normalize_records(keys: List[str], rows: Any, fetch_limit: int, schema: Dict[str, List[str]]) -> tuple[list[dict[str, Any]], bool]:
    records: List[Dict[str, Any]] = []
    rows_list = rows if isinstance(rows, list) else []
    for row in rows_list[:fetch_limit]:
        if isinstance(row, list):
            record = {
                keys[idx] if idx < len(keys) else f"col_{idx}": _sanitize_value(value, schema)
                for idx, value in enumerate(row)
            }
        elif keys:
            record = {keys[0]: _sanitize_value(row, schema)}
        else:
            record = {"value": _sanitize_value(row, schema)}
        records.append(record)
    return records, len(rows_list) > fetch_limit


def _extract_result(reply: Any, target: DbTarget, schema: Dict[str, List[str]]) -> tuple[list[str], list[dict[str, Any]], bool, dict[str, Any]]:
    keys: List[str] = []
    records: List[Dict[str, Any]] = []
    truncated = False
    counters: Dict[str, Any] = {}

    if isinstance(reply, list):
        if len(reply) >= 2 and isinstance(reply[0], list) and isinstance(reply[1], list):
            keys = [_normalize_header_item(item) for item in reply[0]]
            records, truncated = _normalize_records(keys, reply[1], target.fetch_limit, schema)
            if len(reply) >= 3:
                counters = _parse_stats(reply[2])
        elif len(reply) == 1 and _looks_like_stats_row(reply[0]):
            counters = _parse_stats(reply[0])
        elif reply and all(isinstance(item, str) for item in reply):
            counters = _parse_stats(reply)
        else:
            records, truncated = _normalize_records(keys, reply, target.fetch_limit, schema)
    return keys, records, truncated, counters


def _graph_query(conn: _RespConnection, target: DbTarget, statement: str, phase: str) -> Dict[str, Any]:
    graph_name = target.graph_name or target.database or "fuzz_graph"
    started = perf_counter()
    reply = conn.execute("GRAPH.QUERY", graph_name, statement, "--compact")
    schema = _fetch_schema(conn, graph_name)
    keys, records, truncated, counters = _extract_result(reply, target, schema)
    return {
        "phase": phase,
        "statement": statement,
        "elapsed_ms": round((perf_counter() - started) * 1000.0, 3),
        "keys": keys,
        "records": records,
        "records_truncated": truncated,
        "counters": counters,
        "query_type": "cypher(falkordb-resp)",
        "query_timeout_sec": target.query_timeout_sec,
    }


def _execute_query_block(
    conn: _RespConnection,
    target: DbTarget,
    queries: Iterable[str],
    phase: str,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for raw in queries:
        for statement in split_cypher_statements(raw):
            out.append(_graph_query(conn, target, statement, phase))
    return out


def _reset_graph(conn: _RespConnection, target: DbTarget) -> None:
    graph_name = target.graph_name or target.database or "fuzz_graph"
    try:
        conn.execute("GRAPH.DELETE", graph_name)
    except Exception as exc:
        error_text = str(exc).lower()
        tolerated_markers = [
            "unknown graph key",
            "does not exist",
            "no such key",
            "empty key",
            "invalid graph operation on empty key",
        ]
        if not any(marker in error_text for marker in tolerated_markers):
            raise
    conn.execute("GRAPH.QUERY", graph_name, "CREATE (:__stage4_bootstrap__)", "--compact")
    conn.execute("GRAPH.QUERY", graph_name, "MATCH (n) DETACH DELETE n", "--compact")


def _should_attempt_restart_falkordb(target: DbTarget, exc: Exception) -> bool:
    if not target.restart_on_failure or not target.restart_container:
        return False
    text = str(exc).lower()
    markers = [
        "connection refused",
        "connection reset",
        "connection closed",
        "closed unexpectedly",
        "timed out",
        "actively refused",
        "unable to connect",
    ]
    return any(marker in text for marker in markers)


def execute_case_against_target_falkordb(
    case: Dict[str, Any],
    target: DbTarget,
    run_cleanup: bool = True,
) -> Dict[str, Any]:
    started = perf_counter()
    status = "passed"
    error: str | None = None
    phases: List[Dict[str, Any]] = []
    conn = None
    restart_count = 0
    try:
        conn = _connect(target)
        _reset_graph(conn, target)
        if target.pre_case_queries:
            phases.extend(_execute_query_block(conn, target, target.pre_case_queries, "pre_case"))
        phases.extend(_execute_query_block(conn, target, case.get("setup_queries", []), "setup"))
        phases.extend(_execute_query_block(conn, target, [case.get("main_query", "")], "main"))
    except Exception as exc:
        status = "failed"
        error = str(exc)
        if restart_count < target.restart_max_retries and _should_attempt_restart_falkordb(target, exc):
            restart_count += 1
            try:
                _restart_target_container(target)
            except Exception as restart_exc:
                error = f"{error}; restart_failed={restart_exc}"
    finally:
        if run_cleanup:
            try:
                if conn is not None:
                    phases.extend(_execute_query_block(conn, target, case.get("cleanup_queries", []), "cleanup"))
                    if target.post_case_queries:
                        phases.extend(_execute_query_block(conn, target, target.post_case_queries, "post_case"))
            except Exception as cleanup_exc:
                if status == "passed":
                    status = "cleanup_failed"
                    error = str(cleanup_exc)
        try:
            if conn is not None:
                conn.close()
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
