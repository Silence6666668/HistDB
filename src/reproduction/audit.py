from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .manifest import DBMS_NAMES
from .models import ClaimPartition, OracleSpec, PaperClaims, ReportRecord, ReproductionCase


_FAMILY_PREFIX_DBMS = {
    "NEO": "neo4j",
    "MEM": "memgraph",
    "FAL": "falkordb",
    "ARC": "arcadedb",
    "AGE": "apache_age",
}
_FIXED_VERSION_PAPER_SET = "fixed-version-50"


@dataclass(frozen=True, order=True)
class AuditError:
    code: str
    subject: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "subject": self.subject,
            "message": self.message,
        }


@dataclass(frozen=True)
class AuditReport:
    errors: tuple[AuditError, ...]
    family_counts: ClaimPartition
    expected_family_counts: ClaimPartition
    report_counts: ClaimPartition
    expected_report_counts: ClaimPartition

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "counts": {
                "fixed_version_families": {
                    "actual": _partition_dict(self.family_counts),
                    "expected": _partition_dict(self.expected_family_counts),
                },
                "retained_reports": {
                    "actual": _partition_dict(self.report_counts),
                    "expected": _partition_dict(self.expected_report_counts),
                },
            },
            "errors": [error.to_dict() for error in self.errors],
        }

    def format_errors(self) -> str:
        if self.ok:
            return "Catalog audit passed."
        return "\n".join(
            f"[{error.code}] {error.subject}: {error.message}" for error in self.errors
        )


def _partition_dict(partition: ClaimPartition) -> dict[str, int]:
    return {
        **partition.by_dbms(),
        "total": partition.total,
    }


def _population_partition(items: Sequence[ReproductionCase | ReportRecord]) -> ClaimPartition:
    counts = Counter(item.dbms for item in items)
    return ClaimPartition(
        neo4j=counts["neo4j"],
        memgraph=counts["memgraph"],
        falkordb=counts["falkordb"],
        arcadedb=counts["arcadedb"],
        apache_age=counts["apache_age"],
        total=len(items),
    )


def _family_partition(cases: Sequence[ReproductionCase]) -> ClaimPartition:
    representatives: dict[str, ReproductionCase] = {}
    for case in sorted(
        cases,
        key=lambda item: (
            item.family_id,
            item.dbms,
            str(item.source_path),
            item.title,
            item.paper_set,
            item.symptom,
        ),
    ):
        representatives.setdefault(case.family_id, case)
    return _population_partition(tuple(representatives.values()))


def _report_partition(reports: Sequence[ReportRecord]) -> ClaimPartition:
    representatives: dict[str, ReportRecord] = {}
    for report in sorted(
        reports,
        key=lambda item: (
            item.report_id,
            item.dbms,
            item.issue_url,
            str(item.report_path),
            item.maintainer_status,
            item.fix_status,
            item.family_ids,
            item.reproduction_scope,
            item.reason or "",
        ),
    ):
        representatives.setdefault(report.report_id, report)
    return _population_partition(tuple(representatives.values()))


def _family_id_dbms(family_id: str) -> str | None:
    parts = family_id.split("-")
    if len(parts) != 3 or parts[0] != "HISTDB":
        return None
    return _FAMILY_PREFIX_DBMS.get(parts[1])


def _has_nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_report_file(path: object) -> bool:
    if not isinstance(path, Path):
        return False
    try:
        return path.is_file()
    except OSError:
        return False


def _add_count_errors(
    errors: list[AuditError],
    *,
    code: str,
    population: str,
    actual: ClaimPartition,
    expected: ClaimPartition,
) -> None:
    for key in (*DBMS_NAMES, "total"):
        actual_count = actual[key]
        expected_count = expected[key]
        if actual_count != expected_count:
            errors.append(
                AuditError(
                    code=code,
                    subject=f"{population}.{key}",
                    message=f"expected {expected_count}, found {actual_count}",
                )
            )


def _add_duplicate_errors(
    errors: list[AuditError],
    values: Sequence[str],
    *,
    code: str,
    noun: str,
) -> None:
    for value, count in sorted(Counter(values).items()):
        if count > 1:
            errors.append(
                AuditError(
                    code=code,
                    subject=value,
                    message=f"{noun} occurs {count} times",
                )
            )


def audit_catalog(
    cases: Sequence[ReproductionCase],
    reports: Sequence[ReportRecord],
    claims: PaperClaims,
) -> AuditReport:
    case_records = tuple(cases)
    report_records = tuple(reports)
    errors: list[AuditError] = []

    fixed_version_cases = tuple(
        case for case in case_records if case.paper_set == _FIXED_VERSION_PAPER_SET
    )
    family_counts = _family_partition(fixed_version_cases)
    report_counts = _report_partition(report_records)
    _add_count_errors(
        errors,
        code="family_count_mismatch",
        population="fixed_version_families",
        actual=family_counts,
        expected=claims.fixed_version_families,
    )
    _add_count_errors(
        errors,
        code="report_count_mismatch",
        population="retained_reports",
        actual=report_counts,
        expected=claims.retained_reports,
    )

    _add_duplicate_errors(
        errors,
        [case.family_id for case in case_records],
        code="duplicate_family_id",
        noun="family ID",
    )
    _add_duplicate_errors(
        errors,
        [report.report_id for report in report_records],
        code="duplicate_report_id",
        noun="report ID",
    )

    normalized_urls = [
        report.issue_url.strip().casefold()
        for report in report_records
        if _has_nonempty_string(report.issue_url)
    ]
    _add_duplicate_errors(
        errors,
        normalized_urls,
        code="duplicate_issue_url",
        noun="issue URL",
    )

    cases_by_id: dict[str, list[ReproductionCase]] = defaultdict(list)
    for case in case_records:
        cases_by_id[case.family_id].append(case)

    for case in sorted(
        case_records,
        key=lambda item: (item.family_id, item.dbms, str(item.source_path)),
    ):
        if case.paper_set != _FIXED_VERSION_PAPER_SET:
            errors.append(
                AuditError(
                    code="unexpected_paper_set",
                    subject=case.family_id,
                    message=(
                        f"canonical case declares {case.paper_set!r}; "
                        f"expected {_FIXED_VERSION_PAPER_SET!r}"
                    ),
                )
            )
        if not _has_nonempty_string(case.issue.url):
            errors.append(
                AuditError(
                    code="missing_case_issue_url",
                    subject=case.family_id,
                    message="executable family has no issue URL",
                )
            )
        if not _is_report_file(case.issue.report_path):
            errors.append(
                AuditError(
                    code="missing_case_report_file",
                    subject=case.family_id,
                    message=f"case report file does not exist: {case.issue.report_path}",
                )
            )

        expected_dbms = _family_id_dbms(case.family_id)
        if expected_dbms is not None and case.dbms != expected_dbms:
            errors.append(
                AuditError(
                    code="family_id_dbms_mismatch",
                    subject=case.family_id,
                    message=(
                        f"ID prefix identifies {expected_dbms}, but case declares {case.dbms}"
                    ),
                )
            )

        buggy = getattr(case.targets, "buggy", None)
        if buggy is None or not _has_nonempty_string(getattr(buggy, "id", None)):
            errors.append(
                AuditError(
                    code="missing_buggy_target",
                    subject=case.family_id,
                    message="executable family has no buggy target ID",
                )
            )

        oracle = case.oracle
        if (
            not isinstance(oracle, OracleSpec)
            or not _has_nonempty_string(oracle.type)
            or not isinstance(oracle.payload, Mapping)
        ):
            errors.append(
                AuditError(
                    code="missing_structured_oracle",
                    subject=case.family_id,
                    message="executable family has no structured oracle",
                )
            )

    linked_family_ids: set[str] = set()
    known_family_ids = set(cases_by_id)
    for report in sorted(
        report_records,
        key=lambda item: (
            item.report_id,
            item.dbms,
            item.issue_url,
            str(item.report_path),
            item.family_ids,
        ),
    ):
        if not _has_nonempty_string(report.issue_url):
            errors.append(
                AuditError(
                    code="missing_issue_url",
                    subject=report.report_id,
                    message="retained report has no issue URL",
                )
            )
        if not _is_report_file(report.report_path):
            errors.append(
                AuditError(
                    code="missing_report_file",
                    subject=report.report_id,
                    message=f"report file does not exist: {report.report_path}",
                )
            )

        if not report.family_ids:
            reasoned_non_fixed = (
                report.reproduction_scope in {"historical_version", "index_only"}
                and _has_nonempty_string(report.reason)
            )
            if report.reproduction_scope == "paper_fixed_version" or not reasoned_non_fixed:
                errors.append(
                    AuditError(
                        code="unmapped_report",
                        subject=report.report_id,
                        message=(
                            "report has no family mapping and is not a reasoned historical/index-only record"
                        ),
                    )
                )

        for family_id in sorted(set(report.family_ids)):
            subject = f"{report.report_id}:{family_id}"
            linked_cases = cases_by_id.get(family_id, [])
            if not linked_cases:
                errors.append(
                    AuditError(
                        code="unknown_family_reference",
                        subject=subject,
                        message="report references a family absent from the executable catalog",
                    )
                )
            else:
                linked_family_ids.add(family_id)

            expected_dbms_values = {
                case.dbms for case in linked_cases if _has_nonempty_string(case.dbms)
            }
            prefix_dbms = _family_id_dbms(family_id)
            if prefix_dbms is not None:
                expected_dbms_values.add(prefix_dbms)
            if expected_dbms_values and any(
                report.dbms != expected_dbms for expected_dbms in expected_dbms_values
            ):
                expected = ", ".join(sorted(expected_dbms_values))
                errors.append(
                    AuditError(
                        code="report_family_dbms_mismatch",
                        subject=subject,
                        message=f"report declares {report.dbms}; linked family identifies {expected}",
                    )
                )

    for family_id in sorted(known_family_ids - linked_family_ids):
        errors.append(
            AuditError(
                code="family_without_report",
                subject=family_id,
                message="executable family is not linked from a retained report",
            )
        )

    return AuditReport(
        errors=tuple(sorted(errors)),
        family_counts=family_counts,
        expected_family_counts=claims.fixed_version_families,
        report_counts=report_counts,
        expected_report_counts=claims.retained_reports,
    )
