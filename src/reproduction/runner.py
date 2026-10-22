from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .models import OutcomeStatus, ReproductionCase
from .oracles import AttemptEvaluation, evaluate_attempt


Executor = Callable[[ReproductionCase, Any], dict[str, Any]]
HealthProbe = Callable[..., Mapping[str, Any] | bool]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class AttemptResult:
    attempt: int
    status: OutcomeStatus
    explanation: str
    execution: dict[str, Any]
    health: dict[str, Any]
    observed: Any
    expected: Any
    started_at: str
    finished_at: str


@dataclass(frozen=True)
class CaseRunResult:
    case: ReproductionCase
    status: OutcomeStatus
    attempts: tuple[AttemptResult, ...]
    target_role: str
    target_id: str | None
    executor_target: str | None
    target_tag: str | None
    resolved_image_id: str | None
    requested_attempts: int
    explanation: str
    started_at: str
    finished_at: str


def _infer_role(case: ReproductionCase, target: Any) -> str:
    target_name = str(getattr(target, "name", getattr(target, "id", "")))
    return "fixed" if target_name in {item.id for item in case.targets.fixed} else "buggy"


def _default_target_id(case: ReproductionCase, role: str, target: Any) -> str | None:
    target_name = str(getattr(target, "name", getattr(target, "id", ""))) or None
    if role == "buggy":
        return case.targets.buggy.id
    fixed_ids = {item.id for item in case.targets.fixed}
    if target_name in fixed_ids:
        return target_name
    return case.targets.fixed[0].id if case.targets.fixed else None


def _call_probe(probe: HealthProbe, target: Any, when: str) -> Mapping[str, Any] | bool:
    try:
        signature = inspect.signature(probe)
    except (TypeError, ValueError):
        return probe(target, when)
    for args in ((target, when), (target,), ()):
        try:
            signature.bind(*args)
        except TypeError:
            continue
        return probe(*args)
    raise TypeError("health_probe must accept (target, when), (target), or no arguments")


def _record_probe(
    health: dict[str, Any],
    probe: HealthProbe,
    target: Any,
    when: str,
) -> None:
    try:
        snapshot = _call_probe(probe, target, when)
    except Exception as exc:
        health[f"reachable_{when}"] = False
        health[f"probe_error_{when}"] = str(exc)
        return

    if isinstance(snapshot, bool):
        health[f"reachable_{when}"] = snapshot
        return
    if not isinstance(snapshot, Mapping):
        health[f"reachable_{when}"] = False
        health[f"probe_error_{when}"] = "health probe returned an unsupported value"
        return

    health.update({str(key): value for key, value in snapshot.items() if key != "reachable"})
    reachable = snapshot.get("reachable")
    if type(reachable) is bool:
        health[f"reachable_{when}"] = reachable


def _environment_execution(target: Any, role: str, message: str) -> dict[str, Any]:
    return {
        "target": getattr(target, "name", getattr(target, "id", None)),
        "target_role": role,
        "status": "environment_error",
        "error": message,
        "error_category": "connection",
        "setup_complete": False,
        "phases": [],
    }


def _attempt(
    number: int,
    evaluation: AttemptEvaluation,
    execution: dict[str, Any],
    health: dict[str, Any],
    started_at: str,
) -> AttemptResult:
    return AttemptResult(
        attempt=number,
        status=evaluation.status,
        explanation=evaluation.explanation,
        execution=execution,
        health=health,
        observed=evaluation.observed,
        expected=evaluation.expected,
        started_at=started_at,
        finished_at=_now(),
    )


def run_case(
    case: ReproductionCase,
    target: Any,
    executor: Executor,
    health_probe: HealthProbe,
    *,
    target_role: str | None = None,
    target_id: str | None = None,
    target_tag: str | None = None,
    resolved_image_id: str | None = None,
) -> CaseRunResult:
    role = target_role or _infer_role(case, target)
    if role not in {"buggy", "fixed"}:
        raise ValueError("target_role must be 'buggy' or 'fixed'")

    started_at = _now()
    attempts: list[AttemptResult] = []
    for number in range(1, case.reproduction.attempts + 1):
        attempt_started = _now()
        health: dict[str, Any] = {}
        _record_probe(health, health_probe, target, "before")
        if health.get("reachable_before") is False:
            execution = _environment_execution(
                target,
                role,
                str(health.get("probe_error_before") or health.get("error") or "target is unreachable"),
            )
            evaluation = evaluate_attempt(case, execution, health)
            attempts.append(_attempt(number, evaluation, execution, health, attempt_started))
            break

        try:
            execution = executor(case, target)
            if not isinstance(execution, dict):
                raise TypeError("executor must return a mapping")
            execution = dict(execution)
            execution.setdefault("target", getattr(target, "name", getattr(target, "id", None)))
            execution["target_role"] = role
        except Exception as exc:
            execution = _environment_execution(target, role, str(exc))
            execution["exception_type"] = type(exc).__name__

        _record_probe(health, health_probe, target, "after")
        evaluation = evaluate_attempt(case, execution, health)
        attempts.append(_attempt(number, evaluation, execution, health, attempt_started))

    statuses = {attempt.status for attempt in attempts}
    status = attempts[0].status if len(statuses) == 1 else OutcomeStatus.INCONCLUSIVE
    if len(statuses) == 1:
        explanation = attempts[0].explanation
    else:
        labels = ", ".join(sorted(item.value for item in statuses))
        explanation = f"attempt statuses disagreed: {labels}"

    return CaseRunResult(
        case=case,
        status=status,
        attempts=tuple(attempts),
        target_role=role,
        target_id=target_id or _default_target_id(case, role, target),
        executor_target=str(getattr(target, "name", "")) or None,
        target_tag=target_tag,
        resolved_image_id=resolved_image_id,
        requested_attempts=case.reproduction.attempts,
        explanation=explanation,
        started_at=started_at,
        finished_at=_now(),
    )


def missing_fixed_target_result(case: ReproductionCase) -> CaseRunResult:
    timestamp = _now()
    return CaseRunResult(
        case=case,
        status=OutcomeStatus.NOT_REPRODUCED,
        attempts=(),
        target_role="fixed",
        target_id=None,
        executor_target=None,
        target_tag=None,
        resolved_image_id=None,
        requested_attempts=case.reproduction.attempts,
        explanation="case declares no fixed target",
        started_at=timestamp,
        finished_at=timestamp,
    )


def environment_error_result(
    case: ReproductionCase,
    *,
    target_role: str,
    target_id: str | None,
    target_tag: str | None,
    explanation: str,
) -> CaseRunResult:
    timestamp = _now()
    execution = {
        "target": target_id,
        "target_role": target_role,
        "status": "environment_error",
        "error": explanation,
        "error_category": "connection",
        "setup_complete": False,
        "phases": [],
    }
    attempt = AttemptResult(
        attempt=1,
        status=OutcomeStatus.ENVIRONMENT_ERROR,
        explanation=explanation,
        execution=execution,
        health={},
        observed=None,
        expected=None,
        started_at=timestamp,
        finished_at=timestamp,
    )
    return CaseRunResult(
        case=case,
        status=OutcomeStatus.ENVIRONMENT_ERROR,
        attempts=(attempt,),
        target_role=target_role,
        target_id=target_id,
        executor_target=None,
        target_tag=target_tag,
        resolved_image_id=None,
        requested_attempts=case.reproduction.attempts,
        explanation=explanation,
        started_at=timestamp,
        finished_at=timestamp,
    )
