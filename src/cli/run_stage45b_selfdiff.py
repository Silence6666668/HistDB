from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List

from src.common.io_utils import dump_json, ensure_dir
from src.common.jsonl_utils import iter_jsonl, write_jsonl


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Stage 4.5b: compare original/rewrite pairs on one DB target")
    parser.add_argument("--input", default="data/stage4b_selfdiff/execution_results.jsonl")
    parser.add_argument("--output-dir", default="data/stage45b_selfdiff")
    return parser.parse_args()


def _sanitize(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_sanitize(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _sanitize(v) for k, v in value.items()}
    return str(value)


def _main_phase(record: Dict[str, Any]) -> Dict[str, Any] | None:
    for phase in record.get("phases", []) or []:
        if str(phase.get("phase") or "") == "main":
            return phase
    return None


COUNTER_KEYS = [
    "nodes_created",
    "nodes_deleted",
    "relationships_created",
    "relationships_deleted",
    "properties_set",
    "labels_added",
    "labels_removed",
    "indexes_added",
    "indexes_removed",
    "constraints_added",
    "constraints_removed",
]


def _main_counters(record: Dict[str, Any]) -> Dict[str, Any]:
    main = _main_phase(record) or {}
    counters = main.get("counters") or {}
    if not isinstance(counters, dict):
        counters = {}
    return {key: counters.get(key, 0) for key in COUNTER_KEYS}


def _requires_counter_equivalence(original: Dict[str, Any], rewrite: Dict[str, Any]) -> bool:
    for record in (original, rewrite):
        oracle2 = record.get("oracle2") if isinstance(record.get("oracle2"), dict) else {}
        preconditions = oracle2.get("preconditions", []) if isinstance(oracle2, dict) else []
        if any(str(item) == "counter_state_equivalence" for item in preconditions or []):
            return True
    return False


def _result_preview(record: Dict[str, Any]) -> Dict[str, Any]:
    main = _main_phase(record) or {}
    return {
        "keys": main.get("keys", []),
        "records": _sanitize(main.get("records", [])),
        "query_type": main.get("query_type"),
    }


def _fingerprint(record: Dict[str, Any]) -> str:
    preview = _result_preview(record)
    basis = json.dumps(preview, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()


def _unordered_fingerprint(record: Dict[str, Any]) -> str:
    preview = _result_preview(record)
    records = preview.get("records", []) or []
    record_keys = preview.get("keys", []) or []
    normalized_rows = []
    for row in records:
        if isinstance(row, dict):
            normalized_rows.append(
                json.dumps({str(k): _sanitize(row.get(k)) for k in sorted(row.keys())}, ensure_ascii=False, sort_keys=True)
            )
        else:
            normalized_rows.append(json.dumps(_sanitize(row), ensure_ascii=False, sort_keys=True))
    basis = json.dumps(
        {
            "keys": record_keys,
            "records_unordered": sorted(normalized_rows),
            "query_type": preview.get("query_type"),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()


def _deep_unordered(value: Any) -> Any:
    if isinstance(value, list):
        normalized_items = [_deep_unordered(v) for v in value]
        return sorted(normalized_items, key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))
    if isinstance(value, dict):
        return {str(k): _deep_unordered(v) for k, v in sorted(value.items())}
    return _sanitize(value)


def _deep_unordered_fingerprint(record: Dict[str, Any]) -> str:
    preview = _result_preview(record)
    basis = json.dumps(_deep_unordered(preview), ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()


def _error_signature(record: Dict[str, Any]) -> str:
    return str(record.get("error") or "").strip()


def _normalized_error_signature(record: Dict[str, Any]) -> str:
    error = _error_signature(record)
    if not error:
        return ""

    normalized = error
    normalized = re.sub(
        r"\(line\s+\d+,\s+column\s+\d+\s+\(offset:\s*\d+\)\)",
        "(line <LINE>, column <COLUMN> (offset: <OFFSET>))",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r'\n"[\s\S]*?"\n\s*\^+',
        '\n"<QUERY_SNIPPET>"\n^',
        normalized,
        flags=re.DOTALL,
    )
    normalized = re.sub(
        r"\b(Node|Relationship|Path) with id \d+ has been deleted in this transaction\b",
        r"\1 with id <ID> has been deleted in this transaction",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"node<\d+>",
        "node<ID>",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"\b(?:Long|Double|Float|Integer|String|Boolean)\([^)]*\)",
        "Value(<VALUE>)",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"CALL\s*(?:\(\s*\))?\s*\{\s*RETURN\s+\.\.\.\s*\}",
        "CALL <SUBQUERY> { RETURN ... }",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _extract_outer_order_by_terms(query: str) -> List[str]:
    matches = list(re.finditer(r"(?is)\border\s+by\s+", query))
    if not matches:
        return []
    start = matches[-1].end()
    tail = query[start:]
    end_match = re.search(r"(?is)\s+\b(?:skip|limit|return|with|union)\b", tail)
    clause = tail[: end_match.start()] if end_match else tail
    terms = []
    for raw in clause.split(","):
        term = raw.strip()
        if not term:
            continue
        term = re.sub(r"(?is)\s+(?:asc|desc)\s*$", "", term).strip()
        simple = re.fullmatch(r"`?([A-Za-z_]\w*)`?", term)
        if simple:
            terms.append(simple.group(1))
    return terms


def _is_tied_limit_noise(original: Dict[str, Any], rewrite: Dict[str, Any]) -> bool:
    query = str(original.get("main_query") or rewrite.get("main_query") or "")
    lowered = query.lower()
    if " order by " not in lowered or " limit " not in lowered:
        return False
    order_terms = _extract_outer_order_by_terms(query)
    if not order_terms:
        return False

    original_preview = _result_preview(original)
    rewrite_preview = _result_preview(rewrite)
    o_records = original_preview.get("records", []) or []
    r_records = rewrite_preview.get("records", []) or []
    if not o_records or not r_records or len(o_records) != len(r_records):
        return False

    combined = list(o_records) + list(r_records)
    for term in order_terms:
        values = []
        for row in combined:
            if not isinstance(row, dict) or term not in row:
                return False
            values.append(json.dumps(_sanitize(row.get(term)), ensure_ascii=False, sort_keys=True))
        if len(set(values)) != 1:
            return False
    return True


def _is_collect_order_noise(original: Dict[str, Any], rewrite: Dict[str, Any]) -> bool:
    query = str(original.get("main_query") or rewrite.get("main_query") or "")
    lowered = query.lower()
    if "collect(" not in lowered and "collect {" not in lowered:
        return False
    return _deep_unordered_fingerprint(original) == _deep_unordered_fingerprint(rewrite)


def compare_pairs(records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for record in records:
        key = f"{record.get('selfdiff_pair_id')}::{record.get('target')}"
        groups[key].append(record)

    out: List[Dict[str, Any]] = []
    for _, items in sorted(groups.items()):
        by_variant = {str(item.get("selfdiff_variant") or ""): item for item in items}
        original = by_variant.get("original")
        rewrite = by_variant.get("rewrite")
        first = original or rewrite or items[0]

        if not original or not rewrite:
            category = "missing_variant"
        else:
            o_status = str(original.get("status") or "missing")
            r_status = str(rewrite.get("status") or "missing")
            if o_status == "passed" and r_status == "passed":
                if _fingerprint(original) == _fingerprint(rewrite):
                    if _requires_counter_equivalence(original, rewrite) and _main_counters(original) != _main_counters(rewrite):
                        category = "same_result_different_counters"
                    else:
                        category = "same_result"
                elif _unordered_fingerprint(original) == _unordered_fingerprint(rewrite):
                    if _requires_counter_equivalence(original, rewrite) and _main_counters(original) != _main_counters(rewrite):
                        category = "same_result_different_counters"
                    else:
                        category = "same_result_different_order"
                elif _is_tied_limit_noise(original, rewrite):
                    category = "same_result_tied_limit"
                elif _is_collect_order_noise(original, rewrite):
                    category = "same_result_collect_order_only"
                else:
                    category = "different_result"
            elif o_status == "failed" and r_status == "failed":
                o_error = _error_signature(original)
                r_error = _error_signature(rewrite)
                if o_error == r_error:
                    category = "same_error"
                elif _normalized_error_signature(original) == _normalized_error_signature(rewrite):
                    category = "same_error_normalized"
                else:
                    category = "different_error"
            elif o_status == "passed" and r_status == "failed":
                category = "original_passed_rewrite_failed"
            elif o_status == "failed" and r_status == "passed":
                category = "original_failed_rewrite_passed"
            else:
                category = "mixed_status"

        row = {
            "selfdiff_pair_id": first.get("selfdiff_pair_id"),
            "target": first.get("target"),
            "rewrite_type": first.get("rewrite_type"),
            "original_query_id": first.get("original_query_id"),
            "original_case_id": first.get("original_case_id"),
            "category": category,
            "statuses": {
                "original": None if not original else original.get("status"),
                "rewrite": None if not rewrite else rewrite.get("status"),
            },
            "errors": {
                "original": None if not original else _error_signature(original),
                "rewrite": None if not rewrite else _error_signature(rewrite),
            },
            "normalized_errors": {
                "original": None if not original else _normalized_error_signature(original),
                "rewrite": None if not rewrite else _normalized_error_signature(rewrite),
            },
            "original_main_query": None if not original else original.get("main_query"),
            "rewrite_main_query": None if not rewrite else rewrite.get("main_query"),
            "result_diff": {},
            "counter_diff": {},
        }
        if category == "different_result":
            row["result_diff"] = {
                "original": _result_preview(original),
                "rewrite": _result_preview(rewrite),
            }
        elif category == "same_result_different_order":
            row["result_diff"] = {
                "original": _result_preview(original),
                "rewrite": _result_preview(rewrite),
                "note": "Same unordered rows, different row order.",
            }
        elif category == "same_result_tied_limit":
            row["result_diff"] = {
                "original": _result_preview(original),
                "rewrite": _result_preview(rewrite),
                "note": "Different rows selected only under tied ORDER BY values with LIMIT.",
            }
        elif category == "same_result_collect_order_only":
            row["result_diff"] = {
                "original": _result_preview(original),
                "rewrite": _result_preview(rewrite),
                "note": "Same nested values after ignoring unordered COLLECT-derived list element order.",
            }
        elif category == "same_result_different_counters":
            row["counter_diff"] = {
                "original": _main_counters(original),
                "rewrite": _main_counters(rewrite),
                "note": "Same result rows but different write/update counters for a counter-sensitive Oracle-II pair.",
            }
        out.append(row)
    return out


def summarize(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows = list(rows)
    category_counts = Counter(str(row.get("category") or "unknown") for row in rows)
    rewrite_counts = Counter(str(row.get("rewrite_type") or "unknown") for row in rows)
    interesting = [
        row
        for row in rows
        if row.get("category") in {
            "different_result",
            "different_error",
            "same_result_different_counters",
            "original_passed_rewrite_failed",
            "original_failed_rewrite_passed",
        }
    ]
    return {
        "pairs_compared": len(rows),
        "category_counts": dict(category_counts),
        "rewrite_type_counts": dict(rewrite_counts),
        "interesting_cases": interesting,
    }


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    if not input_path.exists():
        raise FileNotFoundError(f"Stage 4.5b input not found: {input_path}")

    rows = list(iter_jsonl(input_path))
    comparisons = compare_pairs(rows)

    output_dir = Path(args.output_dir)
    ensure_dir(output_dir)
    write_jsonl(comparisons, output_dir / "comparison_results.jsonl")

    summary = {
        "input_path": str(input_path),
        "output_dir": str(output_dir),
        **summarize(comparisons),
    }
    dump_json(summary, output_dir / "summary.json", pretty=True)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
