from __future__ import annotations

import re
import socket
from dataclasses import replace
from typing import Any
from urllib.parse import urlparse

from src.cli.run_stage4 import execute_case_for_target
from src.stage4.executor import DbTarget, split_cypher_statements

from .models import ReproductionCase
from .report_oracle import ReportOracle


_SUCCESS_STATUSES = {"completed", "passed", "success", "succeeded"}
_DBMS_ERROR_CODE_RE = re.compile(
    r"\b((?:Neo\.(?:Client|Database|Transient)Error|Memgraph\.(?:Client|Database|Transient)Error)"
    r"\.[A-Za-z0-9_.]+)\b"
)
_FALKORDB_QUERY_ERROR_MARKERS = (
    "failed to create relationship; endpoint was not found",
    "unable to resolve filtered alias",
)


def to_stage4_case(case: ReproductionCase) -> dict[str, Any]:
    """Adapt one canonical reproduction case to the existing Stage 4 shape."""
    return {
        "query_id": case.family_id,
        "case_id": case.provenance.case_id or case.family_id,
        "setup_queries": list(case.test.setup),
        "main_query": case.test.query,
        "cleanup_queries": list(case.test.cleanup),
    }


def to_stage4_report(oracle: ReportOracle) -> dict[str, Any]:
    """Adapt one retained report without introducing a family identity."""
    if oracle.test is None:
        raise ValueError(f"{oracle.report_id}: report has no executable test program")
    setup = list(oracle.test.setup)
    cleanup = list(oracle.test.cleanup)
    if oracle.dbms == "apache_age":
        graph_sql = re.compile(
            r"^\s*SELECT\s+(?:ag_catalog\.)?(?:create_graph|drop_graph)\s*\(",
            re.IGNORECASE,
        )
        setup = [statement for statement in setup if not graph_sql.search(statement)]
        cleanup = [statement for statement in cleanup if not graph_sql.search(statement)]
    return {
        "query_id": oracle.report_id,
        "case_id": oracle.report_id,
        "setup_queries": setup,
        "main_query": oracle.test.query,
        "cleanup_queries": cleanup,
    }


def _target_role(case: ReproductionCase, db_target: DbTarget) -> str:
    if db_target.name in {target.id for target in case.targets.fixed}:
        return "fixed"
    return "buggy"


def _has_main_phase(result: dict[str, Any]) -> bool:
    phases = result.get("phases")
    return isinstance(phases, list) and any(
        isinstance(phase, dict) and phase.get("phase") == "main" for phase in phases
    )


def _setup_completed(case: ReproductionCase, result: dict[str, Any]) -> bool:
    if not case.test.setup:
        return True
    if _has_main_phase(result):
        return True
    return str(result.get("status", "")).casefold() in _SUCCESS_STATUSES


def _attach_structured_error_category(result: dict[str, Any]) -> None:
    if result.get("error_category"):
        return
    error = result.get("error")
    if not isinstance(error, str):
        return
    match = _DBMS_ERROR_CODE_RE.search(error)
    if match:
        result["error_category"] = match.group(1)
        return
    if any(marker in error.casefold() for marker in _FALKORDB_QUERY_ERROR_MARKERS):
        result["error_category"] = "FalkorDB.QueryError"


def execute_with_stage4(
    case: ReproductionCase,
    db_target: DbTarget,
    *,
    target_role: str | None = None,
) -> dict[str, Any]:
    """Execute through Stage 4 and attach current-attempt oracle evidence.

    Stage 4 emits a phase only after that statement completes. Consequently a
    main phase, or a fully passed result, proves that declared setup completed.
    Setup phase records alone are deliberately not used: an executor-internal
    retry may retain them after a later attempt fails before the trigger.
    """
    role = target_role or _target_role(case, db_target)
    if role not in {"buggy", "fixed"}:
        raise ValueError("target_role must be 'buggy' or 'fixed'")

    attempt_target = replace(
        db_target,
        query_timeout_sec=float(case.reproduction.timeout_sec),
    )
    raw_result = execute_case_for_target(to_stage4_case(case), attempt_target)
    if not isinstance(raw_result, dict):
        raise TypeError("Stage 4 executor must return a mapping")

    result = dict(raw_result)
    result.setdefault("target", db_target.name)
    result["target_role"] = role
    result["setup_complete"] = _setup_completed(case, result)
    _attach_structured_error_category(result)
    return result


def execute_report_with_stage4(
    oracle: ReportOracle,
    db_target: DbTarget,
) -> dict[str, Any]:
    """Execute one reviewed report-level oracle through the Stage 4 protocol layer."""
    if oracle.test is None or oracle.oracle is None or oracle.timeout_sec is None:
        raise ValueError(f"{oracle.report_id}: report oracle is not executable")
    if oracle.review.status != "reviewed":
        raise ValueError(f"{oracle.report_id}: report oracle is not reviewed")

    attempt_target = replace(
        db_target,
        query_timeout_sec=float(oracle.timeout_sec),
    )
    stage4 = to_stage4_report(oracle)
    raw_result = execute_case_for_target(stage4, attempt_target)
    if not isinstance(raw_result, dict):
        raise TypeError("Stage 4 executor must return a mapping")

    result = dict(raw_result)
    result.setdefault("target", db_target.name)
    result["report_id"] = oracle.report_id
    result["target_role"] = "buggy"
    phases = result.get("phases")
    completed_setup_statements = (
        sum(
            1
            for phase in phases
            if isinstance(phase, dict) and phase.get("phase") == "setup"
        )
        if isinstance(phases, list)
        else 0
    )
    expected_setup_statements = sum(
        len(split_cypher_statements(statement)) for statement in stage4["setup_queries"]
    )
    result["setup_complete"] = (
        expected_setup_statements == 0
        or _has_main_phase(result)
        or completed_setup_statements >= expected_setup_statements
        or str(result.get("status", "")).casefold() in _SUCCESS_STATUSES
    )
    _attach_structured_error_category(result)
    return result


def probe_target_health(
    target: DbTarget,
    _when: str,
    *,
    timeout_sec: float = 0.5,
) -> dict[str, Any]:
    """Perform a protocol-neutral TCP reachability check for a configured endpoint."""
    host = target.host
    port = target.port
    if (not host or not port) and target.uri:
        parsed = urlparse(target.uri)
        host = host or parsed.hostname
        port = port or parsed.port
    if not host or not port:
        return {"reachable": True, "probe": "not_applicable"}

    try:
        with socket.create_connection((host, port), timeout=timeout_sec):
            pass
    except OSError as exc:
        return {
            "reachable": False,
            "probe": "tcp",
            "error": str(exc),
        }
    return {"reachable": True, "probe": "tcp"}
