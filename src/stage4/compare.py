from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List


NEO4J_CODE_RE = re.compile(r"neo4j_code:\s*([^}]+)")
MESSAGE_RE = re.compile(r"message:\s*([^}]+)")
INT_RE = re.compile(r"^-?(0|[1-9]\d*)$")
FLOAT_RE = re.compile(r"^-?(?:\d+\.\d+|\d+\.\d*[eE][+-]?\d+|\d+[eE][+-]?\d+)$")
QUALIFIED_KEY_RE = re.compile(r"^`?[A-Za-z_]\w*`?(?:\.`?[A-Za-z_]\w*`?)+(?:(__dup\d+))?$")


def _sanitize(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_sanitize(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _sanitize(v) for k, v in value.items()}
    return str(value)


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


def _normalize_key(key: Any) -> str:
    text = str(key).strip().lower()
    match = QUALIFIED_KEY_RE.fullmatch(text)
    if match:
        dup_suffix = match.group(1) or ""
        base = text[: -len(dup_suffix)] if dup_suffix else text
        text = base.split(".")[-1].replace("`", "") + dup_suffix
    return text


def _normalize_age_string(text: str) -> Any:
    value = text.strip()
    if value == "":
        return value
    if value == "null":
        return None
    if value == "true":
        return True
    if value == "false":
        return False
    if INT_RE.fullmatch(value):
        try:
            return int(value)
        except ValueError:
            return value
    if FLOAT_RE.fullmatch(value):
        try:
            return float(value)
        except ValueError:
            return value

    suffix = None
    for candidate in ("::vertex", "::edge", "::path"):
        if value.endswith(candidate):
            suffix = candidate[2:]
            value = value[: -len(candidate)]
            break

    parsed = None
    if (value.startswith('"') and value.endswith('"')) or value.startswith("[") or value.startswith("{"):
        try:
            parsed = json.loads(value)
        except Exception:
            parsed = None

    if parsed is None:
        return text

    normalized = _normalize_result_value(parsed, is_age=True)
    if suffix == "vertex" and isinstance(normalized, dict):
        props = normalized.get("properties")
        if isinstance(props, dict):
            return props
    if suffix == "edge" and isinstance(normalized, dict):
        label = normalized.get("label")
        props = normalized.get("properties")
        if isinstance(props, dict) and props:
            out = {"type": label} if label is not None else {}
            out["properties"] = props
            return out
        if label is not None:
            return str(label)
    if suffix == "path" and isinstance(normalized, list):
        compact: List[Any] = []
        for item in normalized:
            if isinstance(item, dict) and "properties" in item and isinstance(item["properties"], dict):
                compact.append(item["properties"])
            elif isinstance(item, dict) and "label" in item and "start_id" in item and "end_id" in item:
                compact.append(item.get("label"))
            else:
                compact.append(item)
        return compact
    return normalized


def _normalize_result_value(value: Any, is_age: bool = False) -> Any:
    if isinstance(value, list):
        return [_normalize_result_value(v, is_age=is_age) for v in value]
    if isinstance(value, dict):
        return {str(k): _normalize_result_value(v, is_age=is_age) for k, v in value.items()}
    if isinstance(value, str) and is_age:
        return _normalize_age_string(value)
    return _sanitize(value)


def _normalize_preview(record: Dict[str, Any]) -> Dict[str, Any]:
    main = _main_phase(record) or {}
    target_name = str(record.get("target") or "").lower()
    is_age = "age" in target_name
    raw_keys = list(main.get("keys", []))
    raw_records = main.get("records", []) or []
    normalized_keys = _dedupe_keys([_normalize_key(key) for key in raw_keys])
    key_map = {raw: normalized_keys[idx] for idx, raw in enumerate(raw_keys)}
    normalized_records: List[Dict[str, Any]] = []
    for row in raw_records:
        if isinstance(row, dict):
            normalized_row: Dict[str, Any] = {}
            for key, value in row.items():
                normalized_key = key_map.get(key, _normalize_key(key))
                normalized_row[normalized_key] = _normalize_result_value(value, is_age=is_age)
        else:
            normalized_row = {"value": _normalize_result_value(row, is_age=is_age)}
        normalized_records.append(normalized_row)
    return {
        "keys": normalized_keys,
        "records": normalized_records,
        "query_type": main.get("query_type"),
    }


def _main_phase(record: Dict[str, Any]) -> Dict[str, Any] | None:
    for phase in record.get("phases", []) or []:
        if str(phase.get("phase") or "") == "main":
            return phase
    return None


def _main_phase_truncated(record: Dict[str, Any]) -> bool:
    main = _main_phase(record) or {}
    return bool(main.get("records_truncated"))


def _result_fingerprint(record: Dict[str, Any]) -> str | None:
    preview = _normalize_preview(record)
    if not preview["keys"] and not preview["records"] and preview["query_type"] is None:
        return None
    payload = {
        "keys": sorted(preview["keys"]),
        "records": preview["records"],
    }
    basis = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()


def _result_multiset_fingerprint(record: Dict[str, Any]) -> str | None:
    preview = _normalize_preview(record)
    if not preview["keys"] and not preview["records"] and preview["query_type"] is None:
        return None
    row_fingerprints = sorted(
        json.dumps(row, ensure_ascii=False, sort_keys=True)
        for row in preview["records"]
    )
    payload = {
        "keys": sorted(preview["keys"]),
        "records": row_fingerprints,
    }
    basis = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()


def _result_preview(record: Dict[str, Any]) -> Dict[str, Any]:
    return _normalize_preview(record)


def _result_diff(by_target: Dict[str, Dict[str, Any]], target_names: List[str], statuses: Dict[str, str]) -> Dict[str, Any]:
    passed_targets = [name for name in target_names if statuses.get(name) == "passed" and name in by_target]
    if len(passed_targets) < 2:
        return {}
    previews = {name: _result_preview(by_target[name]) for name in passed_targets}
    keys_by_target = {name: previews[name].get("keys", []) for name in passed_targets}
    query_types = {name: previews[name].get("query_type") for name in passed_targets}
    record_counts = {name: len(previews[name].get("records", [])) for name in passed_targets}
    return {
        "keys_by_target": keys_by_target,
        "query_types": query_types,
        "record_counts": record_counts,
        "records_by_target": {name: previews[name].get("records", []) for name in passed_targets},
    }


def _error_signature(text: str | None) -> str:
    error = str(text or "").strip()
    if not error:
        return ""
    code_match = NEO4J_CODE_RE.search(error)
    msg_match = MESSAGE_RE.search(error)
    if code_match and msg_match:
        return f"{code_match.group(1).strip()} | {msg_match.group(1).strip()}"
    if msg_match:
        return msg_match.group(1).strip()
    return error.splitlines()[0].strip()


def classify_case(records: List[Dict[str, Any]], expected_targets: List[str] | None = None) -> Dict[str, Any]:
    by_target = {str(rec.get("target") or ""): rec for rec in records}
    target_names = expected_targets or sorted(by_target)
    statuses = {name: str(by_target.get(name, {}).get("status") or "missing") for name in target_names}
    passed = [name for name, status in statuses.items() if status == "passed"]
    failed = [name for name, status in statuses.items() if status == "failed"]
    missing = [name for name, status in statuses.items() if status == "missing"]
    cleanup_failed = [name for name, status in statuses.items() if status == "cleanup_failed"]

    if missing:
        category = "missing_target_result"
    elif cleanup_failed:
        category = "cleanup_failed"
    elif len(passed) == len(target_names):
        fingerprints = {name: _result_fingerprint(by_target[name]) for name in target_names}
        if len(set(fingerprints.values())) == 1:
            category = "all_passed_same_result"
        else:
            if any(_main_phase_truncated(by_target[name]) for name in target_names):
                category = "all_passed_preview_truncated"
            else:
                multiset_fingerprints = {
                    name: _result_multiset_fingerprint(by_target[name]) for name in target_names
                }
                category = (
                    "all_passed_same_rows_different_order"
                    if len(set(multiset_fingerprints.values())) == 1
                    else "all_passed_different_result"
                )
    elif len(failed) == len(target_names):
        signatures = {name: _error_signature(by_target[name].get("error")) for name in target_names}
        category = "all_failed_same_error" if len(set(signatures.values())) == 1 else "all_failed_different_error"
    else:
        if len(target_names) == 2 and len(passed) == 1 and len(failed) == 1:
            category = f"{passed[0]}_passed_{failed[0]}_failed"
        else:
            category = "mixed_pass_fail"

    first = records[0] if records else {}
    result_fingerprints = {
        name: _result_fingerprint(by_target[name])
        for name in target_names
        if name in by_target and statuses.get(name) == "passed"
    }
    return {
        "query_id": first.get("query_id"),
        "case_id": first.get("case_id"),
        "main_query": first.get("main_query"),
        "category": category,
        "target_statuses": statuses,
        "target_errors": {name: _error_signature(by_target.get(name, {}).get("error")) for name in target_names if name in by_target},
        "result_fingerprints": result_fingerprints,
        "result_diff": _result_diff(by_target, target_names, statuses) if category == "all_passed_different_result" else {},
        "target_results": {name: _result_preview(by_target[name]) for name in target_names if name in by_target and statuses.get(name) == "passed"},
    }


def compare_execution_results(records: Iterable[Dict[str, Any]], expected_targets: List[str] | None = None) -> List[Dict[str, Any]]:
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for record in records:
        key = f"{record.get('query_id')}::{record.get('case_id')}"
        groups[key].append(record)
    out = [classify_case(items, expected_targets=expected_targets) for _, items in sorted(groups.items())]
    return out


def summarize_comparisons(comparisons: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows = list(comparisons)
    category_counts = Counter(str(row.get("category") or "unknown") for row in rows)
    error_clusters: Counter[str] = Counter()
    different_result_cases: List[Dict[str, Any]] = []
    for row in rows:
        for _, error in sorted((row.get("target_errors") or {}).items()):
            if error:
                error_clusters[error] += 1
        if str(row.get("category") or "") == "all_passed_different_result":
            different_result_cases.append({
                "query_id": row.get("query_id"),
                "case_id": row.get("case_id"),
                "main_query": row.get("main_query"),
                "result_diff": row.get("result_diff", {}),
            })
    return {
        "cases_compared": len(rows),
        "category_counts": dict(category_counts),
        "error_clusters": dict(error_clusters),
        "different_result_cases": different_result_cases,
    }
