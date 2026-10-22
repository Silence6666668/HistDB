from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .models import OutcomeStatus, ReproductionCase
from .normalize import normalize_rows, normalize_value


@dataclass(frozen=True)
class AttemptEvaluation:
    status: OutcomeStatus
    explanation: str
    observed: Any = None
    expected: Any = None


CustomOracle = Callable[
    [dict[str, Any], dict[str, Any], dict[str, Any], str], AttemptEvaluation
]

# Entries are added in source review. Manifest data may select a name but cannot
# provide or execute Python code.
CUSTOM_ORACLE_REGISTRY: dict[str, CustomOracle] = {}

_MISSING = object()
_ENVIRONMENT_PHASES = {
    "auth",
    "authentication",
    "connect",
    "connection",
    "health",
    "pre_case",
    "pre_case_schema",
    "setup",
    "startup",
}
_FAILED_PHASE_STATUSES = {
    "cancelled",
    "error",
    "failed",
    "incomplete",
    "partial",
    "timed_out",
    "timeout",
}
_ENVIRONMENT_ERROR_CATEGORIES = {
    "auth": "authentication or authorization failed",
    "auth_error": "authentication or authorization failed",
    "authentication": "authentication or authorization failed",
    "authentication_error": "authentication or authorization failed",
    "authorization": "authentication or authorization failed",
    "authorization_error": "authentication or authorization failed",
    "connection": "transport failure prevented semantic evaluation",
    "connection_error": "transport failure prevented semantic evaluation",
    "network": "transport failure prevented semantic evaluation",
    "network_error": "transport failure prevented semantic evaluation",
    "setup": "setup failed before trigger evaluation",
    "setup_error": "setup failed before trigger evaluation",
    "startup": "setup failed before trigger evaluation",
    "startup_error": "setup failed before trigger evaluation",
    "transport": "transport failure prevented semantic evaluation",
    "transport_error": "transport failure prevented semantic evaluation",
}
_AUTH_MARKERS = (
    "authentication failed",
    "authorization failed",
    "invalid credential",
    "invalid password",
    "login failed",
    "not authorized",
    "permission denied",
    "unauthorized",
)
_TRANSPORT_MARKERS = (
    "actively refused",
    "broken pipe",
    "connection aborted",
    "connection closed",
    "connection refused",
    "connection reset",
    "connection timed out",
    "could not connect",
    "host is unreachable",
    "name resolution",
    "network is unreachable",
    "no route to host",
    "server disconnected",
    "unable to connect",
)
_UNSUPPORTED_MARKERS = (
    "feature not supported",
    "not implemented",
    "not supported",
    "unsupported clause",
    "unsupported feature",
    "unsupported operation",
    "unsupported syntax",
)


def extract_main_phase(result: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return the final structured Stage 4 phase for the trigger query."""
    phases = result.get("phases")
    if not isinstance(phases, list):
        return None
    for phase in reversed(phases):
        if isinstance(phase, dict) and phase.get("phase") == "main":
            return phase
    return None


def _error_text(result: Mapping[str, Any]) -> str:
    error = result.get("error")
    if isinstance(error, str):
        return error
    if isinstance(error, Mapping):
        message = error.get("message")
        return message if isinstance(message, str) else ""
    return ""


def _error_category(result: Mapping[str, Any]) -> Any:
    candidates = [result.get("error_category")]
    error = result.get("error")
    if isinstance(error, Mapping):
        candidates.extend((error.get("category"), error.get("code")))
    for category in candidates:
        if isinstance(category, str) and category.strip():
            return category
    return None


def _failed_environment_phase(result: Mapping[str, Any]) -> str | None:
    phases = result.get("phases")
    if not isinstance(phases, list):
        return None
    for phase in phases:
        if not isinstance(phase, Mapping):
            continue
        name = phase.get("phase")
        if not isinstance(name, str) or name.casefold() not in _ENVIRONMENT_PHASES:
            continue
        status = phase.get("status")
        failed_status = (
            isinstance(status, str) and status.casefold() in _FAILED_PHASE_STATUSES
        )
        explicitly_incomplete = any(
            phase.get(key) is False for key in ("complete", "completed", "ok", "success")
        )
        has_error = phase.get("error") not in (None, "", False)
        if failed_status or explicitly_incomplete or has_error:
            return f"{name} phase failed or was incomplete before trigger evaluation"
    return None


def _structured_environment_category(
    execution_result: Mapping[str, Any], oracle_type: str
) -> str | None:
    category = _error_category(execution_result)
    if category is None:
        return None
    normalized = re.sub(r"[^a-z0-9]+", "_", category.casefold()).strip("_")
    reason = _ENVIRONMENT_ERROR_CATEGORIES.get(normalized)
    if oracle_type == "service_crash" and reason == _ENVIRONMENT_ERROR_CATEGORIES["transport"]:
        return None
    return reason


def _environment_failure(
    execution_result: Mapping[str, Any],
    health: Mapping[str, Any],
    oracle_type: str,
) -> str | None:
    if health.get("reachable_before") is False:
        return "target was unreachable before trigger execution"

    failed_phase = execution_result.get("failed_phase")
    if isinstance(failed_phase, str) and failed_phase.lower() in _ENVIRONMENT_PHASES:
        return f"{failed_phase} phase failed before a semantic result was available"

    phase_failure = _failed_environment_phase(execution_result)
    if phase_failure is not None:
        return phase_failure

    status = str(execution_result.get("status", "")).lower()
    if status in {"environment_error", "setup_failed", "startup_failed", "auth_failed"}:
        return f"executor reported {status}"

    category_failure = _structured_environment_category(execution_result, oracle_type)
    if category_failure is not None:
        return category_failure

    if _error_category(execution_result) is None:
        error = _error_text(execution_result).lower()
        if any(marker in error for marker in _AUTH_MARKERS):
            return "authentication or authorization failed"
        if oracle_type != "service_crash" and any(
            marker in error for marker in _TRANSPORT_MARKERS
        ):
            return "transport failure prevented semantic evaluation"

    if oracle_type != "service_crash" and health.get("reachable_after") is False:
        return "target failed the post-trigger health probe"
    return None


def _unsupported_failure(execution_result: Mapping[str, Any]) -> str | None:
    status = str(execution_result.get("status", "")).lower()
    if status == "unsupported":
        return "executor reported unsupported behavior"
    error = _error_text(execution_result).lower()
    if any(marker in error for marker in _UNSUPPORTED_MARKERS):
        return "target does not support the required query behavior"
    return None


def _semantic_status(matches: bool, target_role: str) -> OutcomeStatus:
    if matches:
        return OutcomeStatus.FIXED if target_role == "fixed" else OutcomeStatus.REPRODUCED
    return OutcomeStatus.NOT_REPRODUCED


def _symptom_status(present: bool, target_role: str) -> OutcomeStatus:
    if target_role == "fixed":
        return OutcomeStatus.NOT_REPRODUCED if present else OutcomeStatus.FIXED
    return OutcomeStatus.REPRODUCED if present else OutcomeStatus.NOT_REPRODUCED


def _expected(payload: Mapping[str, Any], target_role: str) -> Any:
    key = "expected_correct" if target_role == "fixed" else "expected_buggy"
    return payload.get(key, _MISSING)


def _absolute_tolerance(payload: Mapping[str, Any]) -> float:
    value = payload.get("absolute_tolerance", payload.get("tolerance", 0.0))
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ValueError("absolute_tolerance must be a non-negative finite number")
    tolerance = float(value)
    if not math.isfinite(tolerance):
        raise ValueError("absolute_tolerance must be a non-negative finite number")
    return tolerance


def _contains_nan(value: Any) -> bool:
    if isinstance(value, Mapping):
        if value == {"$float": "nan"}:
            return True
        return any(_contains_nan(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_nan(item) for item in value)
    return False


def _values_equal(left: Any, right: Any, tolerance: float) -> bool:
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return set(left) == set(right) and all(
            _values_equal(left[key], right[key], tolerance) for key in left
        )
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _values_equal(left_item, right_item, tolerance)
            for left_item, right_item in zip(left, right)
        )
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left is right
    numeric = (int, float)
    if (
        isinstance(left, numeric)
        and not isinstance(left, bool)
        and isinstance(right, numeric)
        and not isinstance(right, bool)
    ):
        return math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=tolerance)
    return left == right


def _has_unexpected_nan(observed: Any, expected: Any) -> bool:
    if observed == {"$float": "nan"}:
        return expected != {"$float": "nan"}
    if isinstance(observed, Mapping):
        expected_mapping = expected if isinstance(expected, Mapping) else {}
        return any(
            _has_unexpected_nan(value, expected_mapping.get(key, _MISSING))
            for key, value in observed.items()
        )
    if isinstance(observed, list):
        expected_list = expected if isinstance(expected, list) else []
        return any(
            _has_unexpected_nan(
                value,
                expected_list[index] if index < len(expected_list) else _MISSING,
            )
            for index, value in enumerate(observed)
        )
    return False


def _multiset_matches(
    observed: list[Any],
    expected: list[Any],
    predicate: Callable[[Any, Any], bool],
) -> bool:
    if len(observed) != len(expected):
        return False
    candidates = [
        [index for index, expected_row in enumerate(expected) if predicate(row, expected_row)]
        for row in observed
    ]
    if any(not row_candidates for row_candidates in candidates):
        return False

    matched_observed: list[int | None] = [None] * len(expected)

    def augment(observed_index: int, seen: set[int]) -> bool:
        for expected_index in candidates[observed_index]:
            if expected_index in seen:
                continue
            seen.add(expected_index)
            previous = matched_observed[expected_index]
            if previous is None or augment(previous, seen):
                matched_observed[expected_index] = observed_index
                return True
        return False

    return all(
        augment(observed_index, set())
        for observed_index in sorted(range(len(observed)), key=lambda index: len(candidates[index]))
    )


def _compare_values(
    observed: Any,
    expected: Any,
    payload: Mapping[str, Any],
) -> tuple[bool | None, Any, Any]:
    normalized_observed = normalize_value(observed)
    normalized_expected = normalize_value(expected)
    if _has_unexpected_nan(normalized_observed, normalized_expected):
        return None, normalized_observed, normalized_expected
    return (
        _values_equal(normalized_observed, normalized_expected, _absolute_tolerance(payload)),
        normalized_observed,
        normalized_expected,
    )


def _structured_rows(
    execution_result: Mapping[str, Any],
) -> tuple[list[Any] | None, AttemptEvaluation | None]:
    main_phase = extract_main_phase(execution_result)
    if main_phase is None:
        return None, AttemptEvaluation(
            OutcomeStatus.INCONCLUSIVE,
            "executor did not return a structured main phase",
        )
    records = main_phase.get("records")
    if not isinstance(records, list):
        return None, AttemptEvaluation(
            OutcomeStatus.INCONCLUSIVE,
            "main phase did not contain structured records",
        )
    if main_phase.get("records_truncated") is True:
        return None, AttemptEvaluation(
            OutcomeStatus.INCONCLUSIVE,
            "main phase records were truncated",
        )
    return records, None


def _evaluate_exact_rows(
    payload: dict[str, Any],
    execution_result: dict[str, Any],
    _health: dict[str, Any],
    target_role: str,
) -> AttemptEvaluation:
    expected = _expected(payload, target_role)
    if not isinstance(expected, list):
        return AttemptEvaluation(
            OutcomeStatus.INCONCLUSIVE,
            f"oracle has no list expectation for {target_role} target",
        )
    records, failure = _structured_rows(execution_result)
    if failure is not None:
        return failure
    ordered = bool(payload.get("ordered", False))
    observed_rows = normalize_rows(records or [], ordered=ordered)
    expected_rows = normalize_rows(expected, ordered=ordered)
    if ordered:
        unexpected_nan = _has_unexpected_nan(observed_rows, expected_rows)
    else:
        nan_positions_authorized = _multiset_matches(
            observed_rows,
            expected_rows,
            lambda observed, expected: not _has_unexpected_nan(observed, expected),
        )
        unexpected_nan = _contains_nan(observed_rows) and not nan_positions_authorized
    if unexpected_nan:
        return AttemptEvaluation(
            OutcomeStatus.INCONCLUSIVE,
            "observed rows contain NaN without an explicit NaN expectation",
            observed_rows,
            expected_rows,
        )
    tolerance = _absolute_tolerance(payload)
    if ordered:
        matches = _values_equal(observed_rows, expected_rows, tolerance)
    else:
        matches = _multiset_matches(
            observed_rows,
            expected_rows,
            lambda observed, expected: _values_equal(observed, expected, tolerance),
        )
    return AttemptEvaluation(
        _semantic_status(matches, target_role),
        "normalized rows matched" if matches else "normalized rows differed",
        observed_rows,
        expected_rows,
    )


def _evaluate_row_count(
    payload: dict[str, Any],
    execution_result: dict[str, Any],
    _health: dict[str, Any],
    target_role: str,
) -> AttemptEvaluation:
    expected = _expected(payload, target_role)
    if expected is _MISSING:
        return AttemptEvaluation(
            OutcomeStatus.INCONCLUSIVE,
            f"oracle has no count expectation for {target_role} target",
        )
    records, failure = _structured_rows(execution_result)
    if failure is not None:
        return failure
    field = payload.get("field")
    if field is None:
        observed: Any = len(records or [])
    elif (
        isinstance(field, str)
        and records is not None
        and len(records) == 1
        and isinstance(records[0], Mapping)
        and field in records[0]
    ):
        observed = records[0][field]
    else:
        return AttemptEvaluation(
            OutcomeStatus.INCONCLUSIVE,
            "named row-count aggregate was not present in exactly one row",
        )
    matches, normalized_observed, normalized_expected = _compare_values(
        observed, expected, payload
    )
    if matches is None:
        return AttemptEvaluation(
            OutcomeStatus.INCONCLUSIVE,
            "observed count contains NaN without an explicit NaN expectation",
            normalized_observed,
            normalized_expected,
        )
    return AttemptEvaluation(
        _semantic_status(matches, target_role),
        "row count matched" if matches else "row count differed",
        normalized_observed,
        normalized_expected,
    )


def _evaluate_scalar_equals(
    payload: dict[str, Any],
    execution_result: dict[str, Any],
    _health: dict[str, Any],
    target_role: str,
) -> AttemptEvaluation:
    expected = _expected(payload, target_role)
    field = payload.get("field")
    if expected is _MISSING or not isinstance(field, str) or not field:
        return AttemptEvaluation(
            OutcomeStatus.INCONCLUSIVE,
            f"oracle has no scalar expectation and field for {target_role} target",
        )
    records, failure = _structured_rows(execution_result)
    if failure is not None:
        return failure
    if (
        records is None
        or len(records) != 1
        or not isinstance(records[0], Mapping)
        or field not in records[0]
    ):
        return AttemptEvaluation(
            OutcomeStatus.INCONCLUSIVE,
            "scalar field was not present in exactly one structured row",
        )
    matches, observed, normalized_expected = _compare_values(records[0][field], expected, payload)
    if matches is None:
        return AttemptEvaluation(
            OutcomeStatus.INCONCLUSIVE,
            "observed scalar contains NaN without an explicit NaN expectation",
            observed,
            normalized_expected,
        )
    return AttemptEvaluation(
        _semantic_status(matches, target_role),
        "scalar value matched" if matches else "scalar value differed",
        observed,
        normalized_expected,
    )


def _evaluate_error_matches(
    payload: dict[str, Any],
    execution_result: dict[str, Any],
    _health: dict[str, Any],
    target_role: str,
) -> AttemptEvaluation:
    category = payload.get("category")
    pattern = payload.get("pattern")
    if not isinstance(category, str) or not category or not isinstance(pattern, str) or not pattern:
        raise ValueError("error_matches requires non-empty category and pattern")

    error = _error_text(execution_result)
    if not error:
        if target_role == "fixed" and not _successful_trigger(execution_result):
            return AttemptEvaluation(
                OutcomeStatus.INCONCLUSIVE,
                "fixed target did not provide positive successful-trigger evidence",
                {"status": execution_result.get("status"), "error": None},
                {"matched_error": False, "trigger_completed": True},
            )
        return AttemptEvaluation(
            _symptom_status(False, target_role),
            "executor returned no DBMS error",
        )
    actual_category = _error_category(execution_result)
    if actual_category is None:
        return AttemptEvaluation(
            OutcomeStatus.INCONCLUSIVE,
            "DBMS error category was not reliably observed",
            {"category": None, "message": error},
            {"category": category, "pattern": pattern},
        )
    category_matches = str(actual_category).casefold() == category.casefold()
    try:
        pattern_matches = re.search(pattern, error) is not None
    except re.error as exc:
        raise ValueError(f"invalid error_matches pattern: {exc}") from exc
    matches = category_matches and pattern_matches
    if target_role == "fixed" and not matches:
        return AttemptEvaluation(
            OutcomeStatus.INCONCLUSIVE,
            "fixed target returned an unexpected DBMS error",
            {"category": actual_category, "message": error},
            {"category": category, "pattern": pattern},
        )
    return AttemptEvaluation(
        _symptom_status(matches, target_role),
        "DBMS error matched category and pattern" if matches else "DBMS error did not match",
        {"category": actual_category, "message": error},
        {"category": category, "pattern": pattern},
    )


def _evaluate_timeout(
    _payload: dict[str, Any],
    execution_result: dict[str, Any],
    health: dict[str, Any],
    target_role: str,
) -> AttemptEvaluation:
    if health.get("reachable_after") is not True:
        return AttemptEvaluation(
            OutcomeStatus.INCONCLUSIVE,
            "timeout oracle requires a successful post-trigger health probe",
        )
    error = _error_text(execution_result)
    status = str(execution_result.get("status", "")).lower()
    structured_timeout = execution_result.get("timed_out") is True or status in {
        "timeout",
        "timed_out",
    }
    fallback_timeout = re.search(
        r"(?:hard|query|statement)\s+timeout|timed out|timeout exceeded",
        error,
        flags=re.IGNORECASE,
    ) is not None
    present = structured_timeout or fallback_timeout
    if target_role == "fixed" and not present and not _successful_trigger(execution_result):
        return AttemptEvaluation(
            OutcomeStatus.INCONCLUSIVE,
            "fixed target did not provide positive successful-trigger evidence",
            {"timed_out": False, "status": execution_result.get("status"), "error": error or None},
            {"timed_out": False, "trigger_completed": True},
        )
    return AttemptEvaluation(
        _symptom_status(present, target_role),
        "query timeout observed with healthy service" if present else "query timeout not observed",
        {"timed_out": present, "error": error or None},
        {"timed_out": True},
    )


def _successful_trigger(execution_result: Mapping[str, Any]) -> bool:
    status = str(execution_result.get("status", "")).casefold()
    return status in {"completed", "passed", "success", "succeeded"} and extract_main_phase(
        execution_result
    ) is not None


def _evaluate_service_crash(
    _payload: dict[str, Any],
    execution_result: dict[str, Any],
    health: dict[str, Any],
    target_role: str,
) -> AttemptEvaluation:
    if "container_running_after" in health:
        running_key = "container_running_after"
    elif "process_running_after" in health:
        running_key = "process_running_after"
    else:
        running_key = None
    required_values = (health.get("reachable_before"), health.get("reachable_after"))
    if running_key is None or any(type(value) is not bool for value in required_values):
        return AttemptEvaluation(
            OutcomeStatus.INCONCLUSIVE,
            "service-crash oracle requires before, after, and process health evidence",
        )
    running_after = health[running_key]
    if type(running_after) is not bool:
        return AttemptEvaluation(
            OutcomeStatus.INCONCLUSIVE,
            "service-crash oracle requires boolean process health evidence",
        )
    present = (
        health["reachable_before"] is True
        and health["reachable_after"] is False
        and running_after is False
    )
    if health["reachable_after"] is False and running_after is True:
        return AttemptEvaluation(
            OutcomeStatus.INCONCLUSIVE,
            "service was unreachable but process-state evidence did not confirm a crash",
            dict(health),
        )
    if target_role == "fixed" and not present and not _successful_trigger(execution_result):
        return AttemptEvaluation(
            OutcomeStatus.INCONCLUSIVE,
            "fixed target did not provide positive successful-trigger evidence",
            {"health": dict(health), "status": execution_result.get("status")},
            {"service_crash": False, "trigger_completed": True},
        )
    return AttemptEvaluation(
        _symptom_status(present, target_role),
        "service became unreachable and process stopped"
        if present
        else "health evidence did not confirm a service crash",
        dict(health),
        {
            "reachable_before": True,
            "reachable_after": False,
            "container_running_after": False,
        },
    )


def _reference_health(health: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key.removeprefix("reference_"): value
        for key, value in health.items()
        if key.startswith("reference_")
    }


def _evaluate_differential(
    payload: dict[str, Any],
    execution_result: dict[str, Any],
    health: dict[str, Any],
    target_role: str,
) -> AttemptEvaluation:
    reference = execution_result.get("reference_result")
    if not isinstance(reference, dict):
        return AttemptEvaluation(
            OutcomeStatus.INCONCLUSIVE,
            "differential oracle has no structured reference result",
        )
    reference_environment = _environment_failure(
        reference, _reference_health(health), "differential"
    )
    if reference_environment is not None:
        return AttemptEvaluation(
            OutcomeStatus.ENVIRONMENT_ERROR,
            f"reference target: {reference_environment}",
        )
    reference_unsupported = _unsupported_failure(reference)
    if reference_unsupported is not None:
        return AttemptEvaluation(
            OutcomeStatus.UNSUPPORTED,
            f"reference target: {reference_unsupported}",
        )

    target_rows, target_failure = _structured_rows(execution_result)
    if target_failure is not None:
        return target_failure
    reference_rows, reference_failure = _structured_rows(reference)
    if reference_failure is not None:
        return AttemptEvaluation(
            reference_failure.status,
            f"reference target: {reference_failure.explanation}",
        )

    ordered = bool(payload.get("ordered", False))
    normalized_target = normalize_rows(target_rows or [], ordered=ordered)
    normalized_reference = normalize_rows(reference_rows or [], ordered=ordered)
    if _contains_nan(normalized_target) or _contains_nan(normalized_reference):
        return AttemptEvaluation(
            OutcomeStatus.INCONCLUSIVE,
            "differential observation contains NaN without an explicit expectation",
            normalized_target,
            normalized_reference,
        )
    tolerance = _absolute_tolerance(payload)
    if ordered:
        equal = _values_equal(normalized_target, normalized_reference, tolerance)
    else:
        equal = _multiset_matches(
            normalized_target,
            normalized_reference,
            lambda target, reference: _values_equal(target, reference, tolerance),
        )
    return AttemptEvaluation(
        _symptom_status(not equal, target_role),
        "target and reference rows differed" if not equal else "target and reference rows matched",
        normalized_target,
        normalized_reference,
    )


_ORACLE_EVALUATORS: dict[str, CustomOracle] = {
    "exact_rows": _evaluate_exact_rows,
    "row_count": _evaluate_row_count,
    "scalar_equals": _evaluate_scalar_equals,
    "error_matches": _evaluate_error_matches,
    "timeout": _evaluate_timeout,
    "service_crash": _evaluate_service_crash,
    "differential": _evaluate_differential,
}


def _declared_setup_completed(
    case: ReproductionCase, execution_result: Mapping[str, Any]
) -> bool:
    return not case.test.setup or execution_result.get("setup_complete") is True


def evaluate_oracle(
    oracle_type: str,
    payload: dict[str, Any],
    execution_result: dict[str, Any],
    health: dict[str, Any],
    *,
    target_role: str = "buggy",
) -> AttemptEvaluation:
    if target_role not in {"buggy", "fixed"}:
        raise ValueError("target_role must be 'buggy' or 'fixed'")

    custom_evaluator = None
    if oracle_type == "custom":
        name = payload.get("name")
        if not isinstance(name, str) or name not in CUSTOM_ORACLE_REGISTRY:
            raise ValueError(f"custom oracle {name!r} is not registered")
        if any(key in payload for key in ("code", "script", "source")):
            raise ValueError("custom oracle payload contains inline executable code")
        custom_evaluator = CUSTOM_ORACLE_REGISTRY[name]
    elif oracle_type not in _ORACLE_EVALUATORS:
        raise ValueError(f"unknown oracle type: {oracle_type}")

    environment_failure = _environment_failure(execution_result, health, oracle_type)
    if environment_failure is not None:
        return AttemptEvaluation(OutcomeStatus.ENVIRONMENT_ERROR, environment_failure)

    if oracle_type != "error_matches":
        unsupported_failure = _unsupported_failure(execution_result)
        if unsupported_failure is not None:
            return AttemptEvaluation(OutcomeStatus.UNSUPPORTED, unsupported_failure)

    if custom_evaluator is not None:
        return custom_evaluator(payload, execution_result, health, target_role)

    evaluator = _ORACLE_EVALUATORS[oracle_type]
    return evaluator(payload, execution_result, health, target_role)


def evaluate_attempt(
    case: ReproductionCase,
    execution_result: dict[str, Any],
    health: dict[str, Any],
) -> AttemptEvaluation:
    explicit_role = execution_result.get("target_role")
    if explicit_role in {"buggy", "fixed"}:
        target_role = str(explicit_role)
    else:
        target_id = execution_result.get("target")
        fixed_ids = {target.id for target in case.targets.fixed}
        target_role = "fixed" if target_id in fixed_ids else "buggy"

    if (
        execution_result.get("status") == "failed"
        and extract_main_phase(execution_result) is None
        and not _declared_setup_completed(case, execution_result)
    ):
        execution_result = {**execution_result, "failed_phase": "setup"}

    return evaluate_oracle(
        case.oracle.type,
        case.oracle.payload,
        execution_result,
        health,
        target_role=target_role,
    )
