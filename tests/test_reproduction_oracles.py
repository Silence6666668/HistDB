from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from src.reproduction.models import (
    CaseTarget,
    CaseTargets,
    IssueRef,
    OracleSpec,
    OutcomeStatus,
    Provenance,
    ReproductionCase,
    ReproductionPolicy,
    TestProgram as ReproductionTestProgram,
)
from src.reproduction.oracles import (
    CUSTOM_ORACLE_REGISTRY,
    AttemptEvaluation,
    evaluate_attempt,
    evaluate_oracle,
    extract_main_phase,
)


def _result(
    records: list[Any] | None = None,
    *,
    status: str = "passed",
    error: str | None = None,
    truncated: bool = False,
    **extra: Any,
) -> dict[str, Any]:
    phases = []
    if records is not None:
        phases.append(
            {
                "phase": "main",
                "records": records,
                "records_truncated": truncated,
            }
        )
    return {"status": status, "error": error, "phases": phases, **extra}


def _case(oracle_type: str, payload: dict[str, Any]) -> ReproductionCase:
    return ReproductionCase(
        schema_version=1,
        family_id="HISTDB-MEM-001",
        title="Example",
        paper_set="fixed-version-50",
        dbms="memgraph",
        symptom="wrong_result",
        issue=IssueRef("https://example.test/1", "confirmed", Path("report.md")),
        targets=CaseTargets(
            buggy=CaseTarget("memgraph-buggy", "memgraph:buggy"),
            fixed=(CaseTarget("memgraph-fixed", "memgraph:fixed"),),
        ),
        test=ReproductionTestProgram((), "RETURN 1 AS x", ()),
        oracle=OracleSpec(oracle_type, payload),
        reproduction=ReproductionPolicy(3, 10),
        provenance=Provenance(None, None, (), (), ()),
        source_path=Path("case.yaml"),
    )


def test_extract_main_phase_uses_structured_phase_records() -> None:
    main = {"phase": "main", "records": [{"x": 1}], "records_truncated": False}
    result = {
        "display": "x = 999",
        "phases": [{"phase": "setup", "records": []}, main, {"phase": "cleanup"}],
    }

    assert extract_main_phase(result) is main


def test_extract_main_phase_returns_none_when_trigger_has_no_result() -> None:
    assert extract_main_phase({"phases": [{"phase": "setup", "records": []}]}) is None


def test_error_oracle_rejects_transport_error() -> None:
    result = {"status": "failed", "error": "connection refused", "phases": []}

    evaluation = evaluate_oracle(
        oracle_type="error_matches",
        payload={"category": "planner", "pattern": "Internal error"},
        execution_result=result,
        health={"reachable_after": False},
    )

    assert evaluation.status == OutcomeStatus.ENVIRONMENT_ERROR


def test_authentication_failure_precedes_matching_error_pattern() -> None:
    evaluation = evaluate_oracle(
        oracle_type="error_matches",
        payload={"category": "security", "pattern": "authentication failed"},
        execution_result=_result(status="failed", error="authentication failed for user"),
        health={"reachable_after": True},
    )

    assert evaluation.status == OutcomeStatus.ENVIRONMENT_ERROR


@pytest.mark.parametrize(
    ("oracle_type", "payload", "error_category", "extra"),
    [
        (
            "error_matches",
            {"category": "authentication", "pattern": "login rejected"},
            "authentication",
            {},
        ),
        (
            "error_matches",
            {"category": "setup", "pattern": "fixture rejected"},
            "setup",
            {},
        ),
        ("timeout", {}, "transport", {"timed_out": True}),
    ],
)
def test_structured_environment_category_precedes_trigger_symptom(
    oracle_type: str,
    payload: dict[str, Any],
    error_category: str,
    extra: dict[str, Any],
) -> None:
    evaluation = evaluate_oracle(
        oracle_type,
        payload,
        _result(
            status="failed",
            error="login rejected fixture rejected hard timeout exceeded",
            error_category=error_category,
            **extra,
        ),
        {"reachable_before": True, "reachable_after": True},
    )

    assert evaluation.status == OutcomeStatus.ENVIRONMENT_ERROR


def test_structured_semantic_category_suppresses_message_fallback() -> None:
    evaluation = evaluate_oracle(
        "error_matches",
        {"category": "planner", "pattern": "connection reset in planner"},
        _result(
            status="failed",
            error="connection reset in planner",
            error_category="planner",
        ),
        {"reachable_after": True},
    )

    assert evaluation.status == OutcomeStatus.REPRODUCED


def test_setup_failure_precedes_value_oracle() -> None:
    evaluation = evaluate_oracle(
        oracle_type="scalar_equals",
        payload={"field": "x", "expected_buggy": 1},
        execution_result=_result(
            status="failed",
            error="constraint creation failed",
            failed_phase="setup",
        ),
        health={"reachable_before": True, "reachable_after": True},
    )

    assert evaluation.status == OutcomeStatus.ENVIRONMENT_ERROR


@pytest.mark.parametrize("setup_status", ["failed", "incomplete"])
def test_failed_or_incomplete_setup_phase_cannot_satisfy_trigger_oracle(
    setup_status: str,
) -> None:
    evaluation = evaluate_oracle(
        "error_matches",
        {"category": "planner", "pattern": "Internal error"},
        {
            "status": "failed",
            "error": "Internal error while preparing fixture",
            "error_category": "planner",
            "phases": [{"phase": "setup", "status": setup_status}],
        },
        {"reachable_before": True, "reachable_after": True},
    )

    assert evaluation.status == OutcomeStatus.ENVIRONMENT_ERROR


def test_partial_declared_setup_cannot_satisfy_trigger_oracle() -> None:
    case = _case(
        "error_matches",
        {"category": "planner", "pattern": "Internal error"},
    )
    case = replace(
        case,
        test=ReproductionTestProgram(
            ("CREATE (:First)", "CREATE (:Second)"),
            "RETURN 1 AS x",
            (),
        ),
    )
    result = {
        "status": "failed",
        "error": "Internal error while preparing second fixture",
        "error_category": "planner",
        "phases": [{"phase": "setup", "records": []}],
    }

    evaluation = evaluate_attempt(case, result, {"reachable_after": True})

    assert evaluation.status == OutcomeStatus.ENVIRONMENT_ERROR


def test_partial_multi_statement_setup_cannot_satisfy_trigger_oracle() -> None:
    case = _case(
        "error_matches",
        {"category": "planner", "pattern": "Internal error"},
    )
    case = replace(
        case,
        test=ReproductionTestProgram(
            ("CREATE (:First); CREATE (:Second)",),
            "RETURN 1 AS x",
            (),
        ),
    )
    result = {
        "status": "failed",
        "error": "Internal error while preparing second fixture",
        "error_category": "planner",
        "phases": [
            {"phase": "setup", "statement": "CREATE (:First)", "records": []}
        ],
    }

    evaluation = evaluate_attempt(case, result, {"reachable_after": True})

    assert evaluation.status == OutcomeStatus.ENVIRONMENT_ERROR


def test_split_setup_with_explicit_completion_allows_main_error_oracle() -> None:
    case = _case(
        "error_matches",
        {"category": "planner", "pattern": "Internal error"},
    )
    case = replace(
        case,
        test=ReproductionTestProgram(
            ("CREATE (:First); CREATE (:Second)",),
            "RETURN broken() AS x",
            (),
        ),
    )
    result = {
        "status": "failed",
        "error": "Internal error while planning trigger",
        "error_category": "planner",
        "setup_complete": True,
        "phases": [
            {"phase": "setup", "statement": "CREATE (:First)", "records": []},
            {"phase": "setup", "statement": "CREATE (:Second)", "records": []},
        ],
    }

    evaluation = evaluate_attempt(case, result, {"reachable_after": True})

    assert evaluation.status == OutcomeStatus.REPRODUCED


def test_setup_phase_text_without_explicit_completion_is_environment_failure() -> None:
    case = _case(
        "error_matches",
        {"category": "planner", "pattern": "Internal error"},
    )
    case = replace(
        case,
        test=ReproductionTestProgram(
            ("CREATE (:Fixture)",),
            "RETURN broken() AS x",
            (),
        ),
    )
    result = {
        "status": "failed",
        "error": "Internal error while planning trigger",
        "error_category": "planner",
        "phases": [
            {"phase": "setup", "statement": "CREATE (:Fixture)", "records": []}
        ],
    }

    evaluation = evaluate_attempt(case, result, {"reachable_after": True})

    assert evaluation.status == OutcomeStatus.ENVIRONMENT_ERROR


def test_stale_setup_phase_history_without_explicit_completion_is_environment_failure() -> None:
    case = _case(
        "error_matches",
        {"category": "planner", "pattern": "Internal error"},
    )
    case = replace(
        case,
        test=ReproductionTestProgram(
            ("CREATE (:Fixture)",),
            "RETURN broken() AS x",
            (),
        ),
    )
    result = {
        "status": "failed",
        "error": "Internal error after internal restart",
        "error_category": "planner",
        "restart_count": 1,
        "phases": [
            {"phase": "setup", "statement": "CREATE (:Fixture)", "records": []},
            {"phase": "setup", "statement": "CREATE (:Stale)", "records": []},
        ],
    }

    evaluation = evaluate_attempt(case, result, {"reachable_after": True})

    assert evaluation.status == OutcomeStatus.ENVIRONMENT_ERROR


def test_failed_pre_execution_health_probe_is_environment_error() -> None:
    evaluation = evaluate_oracle(
        oracle_type="exact_rows",
        payload={"expected_buggy": []},
        execution_result=_result([]),
        health={"reachable_before": False, "reachable_after": True},
    )

    assert evaluation.status == OutcomeStatus.ENVIRONMENT_ERROR


def test_exact_rows_honors_ordered_and_unordered_modes() -> None:
    result = _result([{"x": 2}, {"x": 1}])

    unordered = evaluate_oracle(
        "exact_rows",
        {"expected_buggy": [{"x": 1}, {"x": 2}], "ordered": False},
        result,
        {},
    )
    ordered = evaluate_oracle(
        "exact_rows",
        {"expected_buggy": [{"x": 1}, {"x": 2}], "ordered": True},
        result,
        {},
    )

    assert unordered.status == OutcomeStatus.REPRODUCED
    assert ordered.status == OutcomeStatus.NOT_REPRODUCED


def test_exact_rows_does_not_infer_match_from_display_text() -> None:
    result = _result([{"x": 2}], display="[{x: 1}]")

    evaluation = evaluate_oracle(
        "exact_rows",
        {"expected_buggy": [{"x": 1}], "ordered": True},
        result,
        {},
    )

    assert evaluation.status == OutcomeStatus.NOT_REPRODUCED


def test_recursive_float_comparison_uses_absolute_tolerance() -> None:
    evaluation = evaluate_oracle(
        "exact_rows",
        {
            "expected_buggy": [{"values": [1.0, {"score": 2.0}]}],
            "ordered": True,
            "absolute_tolerance": 0.01,
        },
        _result([{"values": [1.005, {"score": 2.009}]}]),
        {},
    )

    assert evaluation.status == OutcomeStatus.REPRODUCED


def test_float_outside_absolute_tolerance_does_not_match() -> None:
    evaluation = evaluate_oracle(
        "scalar_equals",
        {"field": "x", "expected_buggy": 1.0, "absolute_tolerance": 0.01},
        _result([{"x": 1.02}]),
        {},
    )

    assert evaluation.status == OutcomeStatus.NOT_REPRODUCED


@pytest.mark.parametrize(("observed", "expected"), [(True, 1), (False, 0.0)])
def test_bool_is_not_equal_to_non_bool_numeric(observed: bool, expected: float) -> None:
    evaluation = evaluate_oracle(
        "scalar_equals",
        {"field": "x", "expected_buggy": expected},
        _result([{"x": observed}]),
        {},
    )

    assert evaluation.status == OutcomeStatus.NOT_REPRODUCED


def test_unexpected_nan_is_inconclusive() -> None:
    evaluation = evaluate_oracle(
        "scalar_equals",
        {"field": "x", "expected_buggy": 1.0},
        _result([{"x": float("nan")}]),
        {},
    )

    assert evaluation.status == OutcomeStatus.INCONCLUSIVE


def test_explicitly_expected_nan_can_match() -> None:
    evaluation = evaluate_oracle(
        "scalar_equals",
        {"field": "x", "expected_buggy": float("nan")},
        _result([{"x": float("nan")}]),
        {},
    )

    assert evaluation.status == OutcomeStatus.REPRODUCED


def test_nan_expectation_only_authorizes_the_same_position() -> None:
    evaluation = evaluate_oracle(
        "scalar_equals",
        {
            "field": "x",
            "expected_buggy": {"authorized": float("nan"), "other": 0.0},
        },
        _result([{"x": {"authorized": 0.0, "other": float("nan")}}]),
        {},
    )

    assert evaluation.status == OutcomeStatus.INCONCLUSIVE


def test_unordered_tolerant_rows_use_multiset_matching_with_duplicates() -> None:
    evaluation = evaluate_oracle(
        "exact_rows",
        {
            "expected_buggy": [{"x": 10.0}, {"x": 10.0}, {"x": 10.1}],
            "ordered": False,
            "absolute_tolerance": 0.11,
        },
        _result([{"x": 9.9}, {"x": 9.9}, {"x": 10.2}]),
        {},
    )

    assert evaluation.status == OutcomeStatus.REPRODUCED


def test_truncated_structured_rows_are_inconclusive() -> None:
    evaluation = evaluate_oracle(
        "row_count",
        {"expected_buggy": 1},
        _result([{"x": 1}], truncated=True),
        {},
    )

    assert evaluation.status == OutcomeStatus.INCONCLUSIVE


@pytest.mark.parametrize(
    ("payload", "records"),
    [
        ({"expected_buggy": 2}, [{"x": 1}, {"x": 2}]),
        ({"field": "count", "expected_buggy": 2}, [{"count": 2}]),
    ],
)
def test_row_count_supports_record_count_and_named_aggregate(
    payload: dict[str, Any], records: list[dict[str, Any]]
) -> None:
    evaluation = evaluate_oracle("row_count", payload, _result(records), {})

    assert evaluation.status == OutcomeStatus.REPRODUCED


def test_scalar_equals_requires_one_row_and_named_field() -> None:
    evaluation = evaluate_oracle(
        "scalar_equals",
        {"field": "answer", "expected_buggy": 42},
        _result([{"answer": 42}]),
        {},
    )

    assert evaluation.status == OutcomeStatus.REPRODUCED
    assert evaluation.observed == 42


def test_error_oracle_matches_structured_category_and_pattern() -> None:
    evaluation = evaluate_oracle(
        "error_matches",
        {"category": "planner", "pattern": r"Internal error.*plan"},
        _result(
            status="failed",
            error="Internal error while building plan",
            error_category="planner",
        ),
        {"reachable_after": True},
    )

    assert evaluation.status == OutcomeStatus.REPRODUCED


def test_error_oracle_rejects_wrong_structured_category() -> None:
    evaluation = evaluate_oracle(
        "error_matches",
        {"category": "planner", "pattern": "Internal error"},
        _result(
            status="failed",
            error="Internal error in runtime",
            error_category="runtime",
        ),
        {"reachable_after": True},
    )

    assert evaluation.status == OutcomeStatus.NOT_REPRODUCED


def test_error_oracle_reads_category_from_structured_error() -> None:
    evaluation = evaluate_oracle(
        "error_matches",
        {"category": "planner", "pattern": "Internal error"},
        {
            "status": "failed",
            "error": {"category": "runtime", "message": "Internal error in runtime"},
            "phases": [],
        },
        {"reachable_after": True},
    )

    assert evaluation.status == OutcomeStatus.NOT_REPRODUCED


def test_error_oracle_without_observed_category_is_inconclusive() -> None:
    evaluation = evaluate_oracle(
        "error_matches",
        {"category": "planner", "pattern": "Internal error"},
        _result(status="failed", error="Internal error while building plan"),
        {"reachable_after": True},
    )

    assert evaluation.status == OutcomeStatus.INCONCLUSIVE


def test_fixed_error_oracle_without_successful_trigger_is_inconclusive() -> None:
    evaluation = evaluate_oracle(
        "error_matches",
        {"category": "planner", "pattern": "Internal error"},
        _result(status="failed", error=None),
        {"reachable_after": True},
        target_role="fixed",
    )

    assert evaluation.status == OutcomeStatus.INCONCLUSIVE


def test_fixed_error_oracle_with_successful_trigger_is_fixed() -> None:
    evaluation = evaluate_oracle(
        "error_matches",
        {"category": "planner", "pattern": "Internal error"},
        _result([{"x": 1}]),
        {"reachable_after": True},
        target_role="fixed",
    )

    assert evaluation.status == OutcomeStatus.FIXED


def test_unsupported_query_is_not_a_wrong_result_miss() -> None:
    evaluation = evaluate_oracle(
        "exact_rows",
        {"expected_buggy": []},
        _result(status="failed", error="Unsupported clause: SEARCH"),
        {"reachable_after": True},
    )

    assert evaluation.status == OutcomeStatus.UNSUPPORTED


def test_timeout_requires_timeout_evidence_and_healthy_service() -> None:
    evaluation = evaluate_oracle(
        "timeout",
        {},
        _result(status="failed", error="Hard timeout exceeded after 10 seconds", timed_out=True),
        {"reachable_before": True, "reachable_after": True},
    )

    assert evaluation.status == OutcomeStatus.REPRODUCED


def test_timeout_with_failed_health_probe_is_environment_error() -> None:
    evaluation = evaluate_oracle(
        "timeout",
        {},
        _result(status="failed", error="query timeout", timed_out=True),
        {"reachable_before": True, "reachable_after": False},
    )

    assert evaluation.status == OutcomeStatus.ENVIRONMENT_ERROR


@pytest.mark.parametrize(
    ("oracle_type", "health"),
    [
        ("timeout", {"reachable_before": True, "reachable_after": True}),
        (
            "service_crash",
            {
                "reachable_before": True,
                "reachable_after": True,
                "container_running_after": True,
            },
        ),
    ],
)
def test_fixed_symptom_oracle_rejects_unrelated_execution_failure(
    oracle_type: str,
    health: dict[str, Any],
) -> None:
    evaluation = evaluate_oracle(
        oracle_type,
        {},
        _result(status="failed", error="syntax error"),
        health,
        target_role="fixed",
    )

    assert evaluation.status == OutcomeStatus.INCONCLUSIVE


@pytest.mark.parametrize(
    ("oracle_type", "health"),
    [
        ("timeout", {"reachable_before": True, "reachable_after": True}),
        (
            "service_crash",
            {
                "reachable_before": True,
                "reachable_after": True,
                "container_running_after": True,
            },
        ),
    ],
)
def test_fixed_symptom_oracle_accepts_successful_trigger_as_correct_behavior(
    oracle_type: str,
    health: dict[str, Any],
) -> None:
    evaluation = evaluate_oracle(
        oracle_type,
        {},
        _result([{"x": 1}]),
        health,
        target_role="fixed",
    )

    assert evaluation.status == OutcomeStatus.FIXED


def test_service_crash_requires_failed_health_probe() -> None:
    result = {"status": "failed", "error": "connection reset", "phases": []}

    evaluation = evaluate_oracle(
        oracle_type="service_crash",
        payload={},
        execution_result=result,
        health={
            "reachable_before": True,
            "reachable_after": False,
            "container_running_after": False,
        },
    )

    assert evaluation.status == OutcomeStatus.REPRODUCED


def test_service_crash_without_post_trigger_probe_is_inconclusive() -> None:
    evaluation = evaluate_oracle(
        "service_crash",
        {},
        _result(status="failed", error="connection reset"),
        {"reachable_before": True},
    )

    assert evaluation.status == OutcomeStatus.INCONCLUSIVE


def test_service_crash_is_not_reproduced_while_service_remains_healthy() -> None:
    evaluation = evaluate_oracle(
        "service_crash",
        {},
        _result(status="failed", error="runtime failure"),
        {
            "reachable_before": True,
            "reachable_after": True,
            "container_running_after": True,
        },
    )

    assert evaluation.status == OutcomeStatus.NOT_REPRODUCED


def test_service_crash_accepts_stopped_process_evidence() -> None:
    evaluation = evaluate_oracle(
        "service_crash",
        {},
        _result(status="failed", error="connection reset"),
        {
            "reachable_before": True,
            "reachable_after": False,
            "process_running_after": False,
        },
    )

    assert evaluation.status == OutcomeStatus.REPRODUCED


def test_unreachable_but_running_service_is_inconclusive_for_crash() -> None:
    evaluation = evaluate_oracle(
        "service_crash",
        {},
        _result(status="failed", error="connection reset"),
        {
            "reachable_before": True,
            "reachable_after": False,
            "container_running_after": True,
        },
    )

    assert evaluation.status == OutcomeStatus.INCONCLUSIVE


def test_differential_compares_target_and_reference_structured_rows() -> None:
    result = _result(
        [{"x": 2}],
        reference_result=_result([{"x": 1}]),
    )

    evaluation = evaluate_oracle(
        "differential",
        {"ordered": True},
        result,
        {"reachable_after": True, "reference_reachable_after": True},
    )

    assert evaluation.status == OutcomeStatus.REPRODUCED


def test_unordered_tolerant_differential_uses_multiset_matching() -> None:
    result = _result(
        [{"x": 9.9}, {"x": 9.9}, {"x": 10.2}],
        reference_result=_result([{"x": 10.0}, {"x": 10.0}, {"x": 10.1}]),
    )

    evaluation = evaluate_oracle(
        "differential",
        {"ordered": False, "absolute_tolerance": 0.11},
        result,
        {"reachable_after": True, "reference_reachable_after": True},
    )

    assert evaluation.status == OutcomeStatus.NOT_REPRODUCED


def test_differential_reference_environment_failure_takes_precedence() -> None:
    result = _result(
        [{"x": 2}],
        reference_result=_result(status="failed", error="connection refused"),
    )

    evaluation = evaluate_oracle(
        "differential",
        {"ordered": True},
        result,
        {"reachable_after": True, "reference_reachable_after": False},
    )

    assert evaluation.status == OutcomeStatus.ENVIRONMENT_ERROR


def test_custom_oracle_rejects_unregistered_registry_name() -> None:
    with pytest.raises(ValueError, match="not registered"):
        evaluate_oracle("custom", {"name": "inline-code"}, _result([]), {})


def test_custom_oracle_dispatches_only_through_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    def reviewed_oracle(
        payload: dict[str, Any],
        execution_result: dict[str, Any],
        health: dict[str, Any],
        target_role: str,
    ) -> AttemptEvaluation:
        return AttemptEvaluation(OutcomeStatus.REPRODUCED, "reviewed evaluator")

    monkeypatch.setitem(CUSTOM_ORACLE_REGISTRY, "reviewed-example", reviewed_oracle)

    evaluation = evaluate_oracle(
        "custom",
        {"name": "reviewed-example"},
        _result([]),
        {},
    )

    assert evaluation.explanation == "reviewed evaluator"


def test_custom_oracle_rejects_inline_executable_code(monkeypatch: pytest.MonkeyPatch) -> None:
    def reviewed_oracle(
        payload: dict[str, Any],
        execution_result: dict[str, Any],
        health: dict[str, Any],
        target_role: str,
    ) -> AttemptEvaluation:
        return AttemptEvaluation(OutcomeStatus.REPRODUCED, "must not run")

    monkeypatch.setitem(CUSTOM_ORACLE_REGISTRY, "reviewed-example", reviewed_oracle)

    with pytest.raises(ValueError, match="inline executable"):
        evaluate_oracle(
            "custom",
            {"name": "reviewed-example", "code": "raise SystemExit"},
            _result([]),
            {},
        )


def test_unknown_oracle_type_is_rejected_before_result_classification() -> None:
    with pytest.raises(ValueError, match="unknown oracle type"):
        evaluate_oracle(
            "guessed",
            {},
            _result(status="failed", error="connection refused"),
            {"reachable_after": False},
        )


def test_evaluate_attempt_uses_correct_expectation_for_declared_fixed_target() -> None:
    case = _case(
        "scalar_equals",
        {"field": "x", "expected_buggy": 2, "expected_correct": 1},
    )
    result = _result([{"x": 1}], target="memgraph-fixed")

    evaluation = evaluate_attempt(case, result, {})

    assert evaluation.status == OutcomeStatus.FIXED
