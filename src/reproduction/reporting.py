from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .runner import AttemptResult, CaseRunResult


_PASSWORD_KEY = re.compile(r"(?:^|_)(?:password|passwd|passphrase|pwd)(?:$|_)", re.IGNORECASE)
_PASSWORD_ENV_KEY = re.compile(r"(?:password|passwd|passphrase|pwd)_env$", re.IGNORECASE)
_REDACTED = "[REDACTED]"


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]
    if hasattr(value, "value") and isinstance(value.value, str):
        return value.value
    return str(value)


def _referenced_password_values(value: Any) -> set[str]:
    names: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, Mapping):
            for key, child in item.items():
                if _PASSWORD_ENV_KEY.search(str(key)) and isinstance(child, str):
                    names.add(child)
                visit(child)
        elif isinstance(item, (list, tuple, set)):
            for child in item:
                visit(child)

    visit(value)
    return {secret for name in names if (secret := os.getenv(name))}


def redact_for_output(value: Any) -> Any:
    """Recursively redact password fields and values sourced from password env vars."""
    serializable = _json_value(value)
    secrets = sorted(_referenced_password_values(serializable), key=len, reverse=True)

    def redact(item: Any) -> Any:
        if isinstance(item, dict):
            output: dict[str, Any] = {}
            for key, child in item.items():
                if _PASSWORD_KEY.search(key) and not _PASSWORD_ENV_KEY.search(key):
                    output[key] = _REDACTED
                else:
                    output[key] = redact(child)
            return output
        if isinstance(item, list):
            return [redact(child) for child in item]
        if isinstance(item, str):
            for secret in secrets:
                item = item.replace(secret, _REDACTED)
            return item
        return item

    return redact(serializable)


def _attempt_record(result: CaseRunResult, attempt: AttemptResult) -> dict[str, Any]:
    return {
        "family_id": result.case.family_id,
        "dbms": result.case.dbms,
        "paper_set": result.case.paper_set,
        "target_id": result.target_id,
        "target_role": result.target_role,
        "target_tag": result.target_tag,
        "resolved_image_id": result.resolved_image_id,
        "attempt": attempt.attempt,
        "status": attempt.status.value,
        "started_at": attempt.started_at,
        "finished_at": attempt.finished_at,
        "health": attempt.health,
        "execution": attempt.execution,
        "oracle": {
            "type": result.case.oracle.type,
            "explanation": attempt.explanation,
            "observed": attempt.observed,
            "expected": attempt.expected,
        },
    }


def _result_record(result: CaseRunResult) -> dict[str, Any]:
    return {
        "family_id": result.case.family_id,
        "title": result.case.title,
        "dbms": result.case.dbms,
        "paper_set": result.case.paper_set,
        "status": result.status.value,
        "explanation": result.explanation,
        "target_role": result.target_role,
        "target_id": result.target_id,
        "executor_target": result.executor_target,
        "target_tag": result.target_tag,
        "resolved_image_id": result.resolved_image_id,
        "requested_attempts": result.requested_attempts,
        "completed_attempts": len(result.attempts),
        "attempt_statuses": [attempt.status.value for attempt in result.attempts],
        "oracle_explanations": [attempt.explanation for attempt in result.attempts],
        "case_source_path": str(result.case.source_path),
        "report_path": str(result.case.issue.report_path),
        "issue_url": result.case.issue.url,
        "started_at": result.started_at,
        "finished_at": result.finished_at,
    }


def _case_manifest_record(result: CaseRunResult) -> dict[str, Any]:
    case = result.case
    return {
        "family_id": case.family_id,
        "dbms": case.dbms,
        "paper_set": case.paper_set,
        "source_path": str(case.source_path),
        "report_path": str(case.issue.report_path),
        "issue_url": case.issue.url,
        "provenance": {
            "query_id": case.provenance.query_id,
            "case_id": case.provenance.case_id,
            "source_artifacts": [str(path) for path in case.provenance.source_artifacts],
            "skeleton_ids": list(case.provenance.skeleton_ids),
            "feature_ids": list(case.provenance.feature_ids),
        },
    }


def _repository_revision() -> str | None:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


def _manifest(results: list[CaseRunResult]) -> dict[str, Any]:
    cases: dict[str, dict[str, Any]] = {}
    targets: dict[tuple[Any, ...], dict[str, Any]] = {}
    for result in results:
        cases.setdefault(result.case.family_id, _case_manifest_record(result))
        key = (
            result.target_id,
            result.target_role,
            result.executor_target,
            result.target_tag,
            result.resolved_image_id,
        )
        targets.setdefault(
            key,
            {
                "id": result.target_id,
                "role": result.target_role,
                "executor_target": result.executor_target,
                "tag": result.target_tag,
                "resolved_image_id": result.resolved_image_id,
            },
        )
    return {
        "schema_version": 1,
        "repository_revision": _repository_revision(),
        "host": {
            "platform": platform.platform(),
            "python": sys.version,
        },
        "target_management": "configured_endpoints_only",
        "selected_cases": [cases[key] for key in sorted(cases)],
        "targets": [targets[key] for key in sorted(targets, key=lambda item: str(item))],
    }


def _summary(results: list[CaseRunResult]) -> dict[str, Any]:
    outcomes = Counter(result.status.value for result in results)
    dbms = Counter(result.case.dbms for result in results)
    paper_sets = Counter(result.case.paper_set for result in results)
    return {
        "case_target_results": len(results),
        "outcomes": dict(sorted(outcomes.items())),
        "dbms": dict(sorted(dbms.items())),
        "paper_sets": dict(sorted(paper_sets.items())),
        "families": {
            result.case.family_id: result.status.value
            for result in sorted(results, key=lambda item: (item.case.family_id, item.target_id or ""))
        },
    }


def _summary_markdown(results: list[CaseRunResult], summary: dict[str, Any]) -> str:
    lines = [
        "# Reproduction Summary",
        "",
        f"Case-target results: {summary['case_target_results']}",
        "",
        "| Family | DBMS | Target | Outcome | Attempts | Evidence |",
        "|---|---|---|---|---:|---|",
    ]
    for result in sorted(results, key=lambda item: (item.case.family_id, item.target_id or "")):
        source = str(result.case.source_path).replace("\\", "/")
        report = str(result.case.issue.report_path).replace("\\", "/")
        evidence = f"[case]({source}), [report]({report}), [issue]({result.case.issue.url})"
        lines.append(
            "| "
            + " | ".join(
                (
                    result.case.family_id,
                    result.case.dbms,
                    result.target_id or "none",
                    result.status.value,
                    str(len(result.attempts)),
                    evidence,
                )
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def _atomic_write(path: Path, text: str) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _json_text(value: Any) -> str:
    return json.dumps(redact_for_output(value), indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def _jsonl_text(records: Iterable[dict[str, Any]]) -> str:
    return "".join(
        json.dumps(redact_for_output(record), sort_keys=True, ensure_ascii=True) + "\n"
        for record in records
    )


def write_run_artifacts(
    result_set: Iterable[CaseRunResult],
    output_dir: str | Path,
) -> dict[str, Path]:
    results = list(result_set)
    directory = Path(output_dir).resolve()
    names = (
        "run-manifest.json",
        "attempts.jsonl",
        "results.jsonl",
        "summary.json",
        "summary.md",
    )
    paths = {name: directory / name for name in names}
    source_paths = {result.case.source_path.resolve() for result in results}
    collision = source_paths.intersection(path.resolve() for path in paths.values())
    if collision:
        raise ValueError(f"output would overwrite source manifest: {sorted(collision)[0]}")

    summary = _summary(results)
    payloads = {
        "run-manifest.json": _json_text(_manifest(results)),
        "attempts.jsonl": _jsonl_text(
            _attempt_record(result, attempt)
            for result in results
            for attempt in result.attempts
        ),
        "results.jsonl": _jsonl_text(_result_record(result) for result in results),
        "summary.json": _json_text(summary),
        "summary.md": str(redact_for_output(_summary_markdown(results, summary))),
    }

    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        _atomic_write(paths[name], payloads[name])
    return paths
