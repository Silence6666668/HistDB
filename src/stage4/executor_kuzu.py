from __future__ import annotations

import re
import shutil
import tempfile
import uuid
from pathlib import Path
from time import perf_counter
from typing import Any, Dict, Iterable, List, Tuple

import kuzu

from src.stage4.executor import DbTarget, split_cypher_statements


NODE_PATTERN_RE = re.compile(
    r"\(\s*(?:(?P<var>[A-Za-z_][A-Za-z0-9_]*)\s*)?"
    r":\s*(?P<label>`[^`]+`|[A-Za-z_][A-Za-z0-9_]*)"
    r"\s*(?P<props>\{[^{}]*\})?\s*\)"
)

REL_PATTERN_RE = re.compile(
    r"\(\s*(?:[A-Za-z_][A-Za-z0-9_]*\s*)?:\s*(?P<src>`[^`]+`|[A-Za-z_][A-Za-z0-9_]*)[^)]*\)"
    r"\s*-\s*\[\s*(?:[A-Za-z_][A-Za-z0-9_]*\s*)?:\s*(?P<rel>`[^`]+`|[A-Za-z_][A-Za-z0-9_]*)(?P<rel_tail>[^\]]*)\]\s*->\s*"
    r"\(\s*(?:[A-Za-z_][A-Za-z0-9_]*\s*)?:\s*(?P<dst>`[^`]+`|[A-Za-z_][A-Za-z0-9_]*)[^)]*\)"
)

UNDIRECTED_REL_PATTERN_RE = re.compile(
    r"\(\s*(?:[A-Za-z_][A-Za-z0-9_]*\s*)?:\s*(?P<src>`[^`]+`|[A-Za-z_][A-Za-z0-9_]*)[^)]*\)"
    r"\s*-\s*\[\s*(?:[A-Za-z_][A-Za-z0-9_]*\s*)?:\s*(?P<rel>`[^`]+`|[A-Za-z_][A-Za-z0-9_]*)(?P<rel_tail>[^\]]*)\]\s*-\s*"
    r"\(\s*(?:[A-Za-z_][A-Za-z0-9_]*\s*)?:\s*(?P<dst>`[^`]+`|[A-Za-z_][A-Za-z0-9_]*)[^)]*\)"
)

VAR_REL_PATTERN_RE = re.compile(
    r"\(\s*(?P<src_var>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?:\s*:\s*(?P<src_label>`[^`]+`|[A-Za-z_][A-Za-z0-9_]*))?[^)]*\)"
    r"\s*-\s*\[\s*(?:[A-Za-z_][A-Za-z0-9_]*\s*)?:\s*(?P<rel>`[^`]+`|[A-Za-z_][A-Za-z0-9_]*)(?P<rel_tail>[^\]]*)\]\s*(?P<arrow>->|-)\s*"
    r"\(\s*(?P<dst_var>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?:\s*:\s*(?P<dst_label>`[^`]+`|[A-Za-z_][A-Za-z0-9_]*))?[^)]*\)"
)

CYPHER_PREFIX_RE = re.compile(r"(?is)^\s*CYPHER\s+(?:\d+|[A-Za-z_][A-Za-z0-9_]*)\s+")
CLAUSE_KEYWORD_RE = re.compile(
    r"(?is)\b(MATCH|OPTIONAL\s+MATCH|CREATE|MERGE|WITH|RETURN|WHERE|SET|DELETE|DETACH\s+DELETE|UNWIND|CALL|ORDER\s+BY|LIMIT|SKIP|UNION|REMOVE|FOREACH)\b"
)


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_sanitize_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _sanitize_value(v) for k, v in value.items()}
    return str(value)


def _clean_name(raw: str) -> str:
    text = raw.strip()
    if text.startswith("`") and text.endswith("`"):
        text = text[1:-1].replace("``", "`")
    return text


def _quote_name(name: str) -> str:
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        return name
    return "`" + name.replace("`", "``") + "`"


def _split_top_level_commas(text: str) -> List[str]:
    out: List[str] = []
    buf: List[str] = []
    depth = 0
    in_single = False
    in_double = False
    escape = False
    for ch in text:
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
                    out.append(item)
                buf = []
                continue
        buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def _infer_type(value_expr: str) -> str | None:
    value = value_expr.strip()
    if re.fullmatch(r"(?is)null", value):
        return None
    if re.fullmatch(r"(?is)true|false", value):
        return "BOOL"
    if re.fullmatch(r"[-+]?\d+", value):
        return "INT64"
    if re.fullmatch(r"[-+]?(?:\d+\.\d*|\d*\.\d+)(?:[eE][-+]?\d+)?", value):
        return "DOUBLE"
    if re.fullmatch(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"", value):
        return "STRING"
    return None


def _parse_props(props: str | None) -> Dict[str, str]:
    if not props:
        return {}
    inner = props.strip()[1:-1].strip()
    if not inner:
        return {}
    out: Dict[str, str] = {}
    for item in _split_top_level_commas(inner):
        if ":" not in item:
            continue
        key, value = item.split(":", 1)
        key = _clean_name(key.strip())
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*|`[^`]+`", key):
            continue
        inferred = _infer_type(value)
        if inferred:
            out[key] = inferred
    return out


def _merge_type(current: str | None, new_type: str) -> str:
    if current is None:
        return new_type
    if current == new_type:
        return current
    if {current, new_type} <= {"INT64", "DOUBLE"}:
        return "DOUBLE"
    return "STRING"


def _infer_schema(queries: Iterable[str]) -> tuple[Dict[str, Dict[str, str]], Dict[str, Dict[str, Any]]]:
    node_props: Dict[str, Dict[str, str]] = {}
    rels: Dict[str, Dict[str, Any]] = {}
    var_labels: Dict[str, str] = {}
    for raw in queries:
        for stmt in split_cypher_statements(raw):
            stmt = _strip_cypher_prefix(stmt)
            for match in NODE_PATTERN_RE.finditer(stmt):
                label = _clean_name(match.group("label"))
                var = match.group("var")
                if var:
                    var_labels[var] = label
                props = node_props.setdefault(label, {})
                for key, typ in _parse_props(match.group("props")).items():
                    if key == "kuzu_pk":
                        continue
                    props[key] = _merge_type(props.get(key), typ)
            for regex in (REL_PATTERN_RE, UNDIRECTED_REL_PATTERN_RE):
                for match in regex.finditer(stmt):
                    src = _clean_name(match.group("src"))
                    dst = _clean_name(match.group("dst"))
                    rel = _clean_name(match.group("rel"))
                    entry = rels.setdefault(rel, {"pairs": set(), "props": {}})
                    entry["pairs"].add((src, dst))
                    if regex is UNDIRECTED_REL_PATTERN_RE:
                        entry["pairs"].add((dst, src))
                    rel_tail = match.group("rel_tail") or ""
                    prop_match = re.search(r"\{[^{}]*\}", rel_tail)
                    for key, typ in _parse_props(prop_match.group(0) if prop_match else None).items():
                        entry["props"][key] = _merge_type(entry["props"].get(key), typ)
            for match in VAR_REL_PATTERN_RE.finditer(stmt):
                src = _clean_name(match.group("src_label")) if match.group("src_label") else var_labels.get(match.group("src_var"))
                dst = _clean_name(match.group("dst_label")) if match.group("dst_label") else var_labels.get(match.group("dst_var"))
                if not src or not dst:
                    continue
                rel = _clean_name(match.group("rel"))
                entry = rels.setdefault(rel, {"pairs": set(), "props": {}})
                entry["pairs"].add((src, dst))
                if match.group("arrow") == "-":
                    entry["pairs"].add((dst, src))
                rel_tail = match.group("rel_tail") or ""
                prop_match = re.search(r"\{[^{}]*\}", rel_tail)
                for key, typ in _parse_props(prop_match.group(0) if prop_match else None).items():
                    entry["props"][key] = _merge_type(entry["props"].get(key), typ)
    return node_props, rels


def _schema_queries(node_props: Dict[str, Dict[str, str]], rels: Dict[str, Dict[str, Any]]) -> List[str]:
    queries: List[str] = []
    for label in sorted(node_props):
        columns = ["kuzu_pk INT64"]
        for key, typ in sorted(node_props[label].items()):
            columns.append(f"{_quote_name(key)} {typ}")
        columns.append("PRIMARY KEY(kuzu_pk)")
        queries.append(f"CREATE NODE TABLE {_quote_name(label)}({', '.join(columns)})")
    for rel in sorted(rels):
        pairs = sorted(rels[rel]["pairs"])
        valid_pairs = [(src, dst) for src, dst in pairs if src in node_props and dst in node_props]
        if not valid_pairs:
            continue
        parts = [f"FROM {_quote_name(src)} TO {_quote_name(dst)}" for src, dst in valid_pairs]
        for key, typ in sorted(rels[rel]["props"].items()):
            parts.append(f"{_quote_name(key)} {typ}")
        queries.append(f"CREATE REL TABLE {_quote_name(rel)}({', '.join(parts)})")
    return queries


def _inject_kuzu_pk(statement: str, counter: List[int]) -> str:
    def repl(match: re.Match[str]) -> str:
        text = match.group(0)
        props = match.group("props")
        if props and re.search(r"(?i)(?:^|[,{]\s*)kuzu_pk\s*:", props):
            return text
        counter[0] += 1
        pk = counter[0]
        if props:
            start, end = match.span("props")
            rel_start, rel_end = start - match.start(), end - match.start()
            inner = props[1:-1].strip()
            if not inner:
                inserted = f"{{kuzu_pk: {pk}}}"
            else:
                inserted = f"{{kuzu_pk: {pk}, {inner}}}"
            return text[:rel_start] + inserted + text[rel_end:]
        return text[:-1].rstrip() + f" {{kuzu_pk: {pk}}})"

    pieces: List[str] = []
    matches = list(CLAUSE_KEYWORD_RE.finditer(statement))
    if not matches:
        return statement
    cursor = 0
    for idx, match in enumerate(matches):
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(statement)
        pieces.append(statement[cursor:start])
        segment = statement[start:end]
        keyword = re.sub(r"\s+", " ", match.group(1).strip().upper())
        if keyword in {"CREATE", "MERGE"}:
            segment = NODE_PATTERN_RE.sub(repl, segment)
        pieces.append(segment)
        cursor = end
    pieces.append(statement[cursor:])
    return "".join(pieces)


def _strip_cypher_prefix(statement: str) -> str:
    return CYPHER_PREFIX_RE.sub("", statement, count=1)


def _execute_statement(
    conn: kuzu.Connection,
    target: DbTarget,
    statement: str,
    phase: str,
    pk_counter: List[int],
) -> Dict[str, Any]:
    started = perf_counter()
    original_statement = statement
    statement = _inject_kuzu_pk(_strip_cypher_prefix(statement.strip().rstrip(";")), pk_counter)
    result = conn.execute(statement)
    keys = list(result.get_column_names())
    rows: List[Dict[str, Any]] = []
    truncated = False
    limit = max(0, target.fetch_limit)
    while result.has_next():
        row = result.get_next()
        if len(rows) >= limit:
            truncated = True
            break
        rows.append({keys[idx]: _sanitize_value(row[idx]) for idx in range(min(len(keys), len(row)))})
    phase_record: Dict[str, Any] = {
        "phase": phase,
        "statement": statement,
        "elapsed_ms": round((perf_counter() - started) * 1000.0, 3),
        "keys": keys,
        "records": rows,
        "records_truncated": truncated,
        "counters": {},
        "query_type": None,
        "query_timeout_sec": target.query_timeout_sec,
    }
    if original_statement != statement:
        phase_record["original_statement"] = original_statement
    return phase_record


def _execute_query_block(
    conn: kuzu.Connection,
    target: DbTarget,
    queries: Iterable[str],
    phase: str,
    pk_counter: List[int],
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for raw in queries:
        for statement in split_cypher_statements(raw):
            out.append(_execute_statement(conn, target, statement, phase, pk_counter))
    return out


def _make_db_path(target: DbTarget, case: Dict[str, Any]) -> tuple[Path, bool]:
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(case.get("case_id") or case.get("query_id") or uuid.uuid4().hex))
    if target.database:
        root = Path(target.database)
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{safe_id}_{uuid.uuid4().hex[:8]}"
        return path, True
    path = Path(tempfile.gettempdir()) / f"kuzu_stage4_{safe_id}_{uuid.uuid4().hex[:8]}"
    return path, True


def execute_case_against_target_kuzu(
    case: Dict[str, Any],
    target: DbTarget,
    run_cleanup: bool = True,
) -> Dict[str, Any]:
    started = perf_counter()
    status = "passed"
    error: str | None = None
    phases: List[Dict[str, Any]] = []
    db_path, remove_after = _make_db_path(target, case)
    pk_counter = [0]

    all_queries: List[str] = []
    all_queries.extend(target.pre_case_queries or [])
    all_queries.extend(case.get("setup_queries", []))
    all_queries.append(case.get("main_query", ""))
    node_props, rels = _infer_schema(all_queries)
    schema = _schema_queries(node_props, rels)

    try:
        if db_path.exists():
            shutil.rmtree(db_path, ignore_errors=True)
        db = kuzu.Database(str(db_path))
        conn = kuzu.Connection(db)
        try:
            phases.extend(_execute_query_block(conn, target, schema, "schema", pk_counter))
            if target.pre_case_queries:
                phases.extend(_execute_query_block(conn, target, target.pre_case_queries, "pre_case", pk_counter))
            phases.extend(_execute_query_block(conn, target, case.get("setup_queries", []), "setup", pk_counter))
            phases.extend(_execute_query_block(conn, target, [case.get("main_query", "")], "main", pk_counter))
            if run_cleanup:
                phases.extend(_execute_query_block(conn, target, case.get("cleanup_queries", []), "cleanup", pk_counter))
                if target.post_case_queries:
                    phases.extend(_execute_query_block(conn, target, target.post_case_queries, "post_case", pk_counter))
        except Exception as exc:
            status = "failed"
            error = str(exc)
        finally:
            del conn
            del db
    except Exception as exc:
        status = "failed"
        error = str(exc)
    finally:
        if remove_after:
            shutil.rmtree(db_path, ignore_errors=True)

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
        "kuzu_schema_queries": schema,
    }
