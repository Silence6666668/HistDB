from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from .manifest import DBMS_NAMES, ORACLE_TYPES
from .models import OracleSpec, TestProgram


PROTOCOL_BY_DBMS = {
    "neo4j": "bolt",
    "memgraph": "bolt",
    "falkordb": "resp",
    "arcadedb": "http",
    "apache_age": "postgres_sql",
}
REVIEW_STATUSES = {"pending", "reviewed", "missing_oracle"}
FOUND_VERSION_STATUSES = {"reviewed", "unresolved"}
_TOP_LEVEL_FIELDS = {
    "schema_version",
    "report_id",
    "dbms",
    "issue_url",
    "found_version",
    "protocol",
    "test",
    "oracle",
    "timeout_sec",
    "provenance",
    "review",
    "missing_oracle_reason",
}


class ReportOracleError(ValueError):
    """A report-level historical oracle is invalid."""


@dataclass(frozen=True)
class FoundVersionLink:
    status: str
    version: str | None
    inventory_path: Path


@dataclass(frozen=True)
class OracleProvenance:
    source_artifacts: tuple[Path, ...]
    test_source: str
    oracle_evidence: tuple[str, ...]


@dataclass(frozen=True)
class OracleReview:
    status: str
    reviewer: str | None
    reviewed_at: date | None
    notes: str | None


@dataclass(frozen=True)
class ReportOracle:
    schema_version: int
    report_id: str
    dbms: str
    issue_url: str
    found_version: FoundVersionLink | None
    protocol: str
    test: TestProgram | None
    oracle: OracleSpec | None
    timeout_sec: int | None
    provenance: OracleProvenance
    review: OracleReview
    missing_oracle_reason: str | None
    source_path: Path


def _mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReportOracleError(f"{field} must be a mapping")
    return value


def _text(value: object, field: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ReportOracleError(f"{field} must be a non-empty string")
    return value.strip()


def _string_list(value: object, field: str, *, nonempty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ReportOracleError(f"{field} must be a list")
    result = tuple(_text(item, f"{field}[{index}]") or "" for index, item in enumerate(value))
    if nonempty and not result:
        raise ReportOracleError(f"{field} must not be empty")
    return result


def _local_existing_path(value: object, field: str, repo_root: Path) -> Path:
    raw = _text(value, field) or ""
    root = repo_root.resolve()
    candidate = Path(raw)
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError as error:
        raise ReportOracleError(f"{field} must resolve beneath repo_root") from error
    normalized = relative.as_posix().casefold()
    if normalized.startswith("reproduction/cases/"):
        raise ReportOracleError(f"{field} must not reference a family manifest")
    if not resolved.is_file():
        raise ReportOracleError(f"{field} does not exist: {raw}")
    return resolved


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
        raise ReportOracleError(f"cannot load report oracle {path}: {error}") from error
    return _mapping(value, "report oracle")


def load_report_oracle(
    path: str | Path,
    repo_root: str | Path,
    *,
    require_found_version: bool = True,
) -> ReportOracle:
    root = Path(repo_root).resolve()
    source_path = Path(path).resolve()
    data = _load_yaml(source_path)
    unknown = sorted(set(data) - _TOP_LEVEL_FIELDS)
    if unknown:
        raise ReportOracleError(f"unknown fields: {', '.join(unknown)}")
    if data.get("schema_version") != 1:
        raise ReportOracleError("schema_version must be 1")
    report_id = _text(data.get("report_id"), "report_id") or ""
    dbms = _text(data.get("dbms"), "dbms") or ""
    if dbms not in DBMS_NAMES:
        raise ReportOracleError(f"dbms must be one of {', '.join(DBMS_NAMES)}")
    issue_url = _text(data.get("issue_url"), "issue_url") or ""
    if not issue_url.startswith(("https://", "http://")):
        raise ReportOracleError("issue_url must be an HTTP(S) URL")

    found_version: FoundVersionLink | None = None
    if require_found_version:
        found_data = _mapping(data.get("found_version"), "found_version")
        found_status = _text(found_data.get("status"), "found_version.status") or ""
        if found_status not in FOUND_VERSION_STATUSES:
            raise ReportOracleError("found_version.status must be reviewed or unresolved")
        found_value = _text(
            found_data.get("version"), "found_version.version", optional=True
        )
        if found_status == "reviewed" and not found_value:
            raise ReportOracleError("reviewed found_version requires version")
        if found_status == "unresolved" and found_value:
            raise ReportOracleError("unresolved found_version cannot contain version")
        found_version = FoundVersionLink(
            status=found_status,
            version=found_value,
            inventory_path=_local_existing_path(
                found_data.get("inventory_path"), "found_version.inventory_path", root
            ),
        )

    protocol = _text(data.get("protocol"), "protocol") or ""
    if protocol != PROTOCOL_BY_DBMS[dbms]:
        raise ReportOracleError(
            f"protocol for {dbms} must be {PROTOCOL_BY_DBMS[dbms]}"
        )

    provenance_data = _mapping(data.get("provenance"), "provenance")
    raw_artifacts = provenance_data.get("source_artifacts")
    if not isinstance(raw_artifacts, list) or not raw_artifacts:
        raise ReportOracleError("provenance.source_artifacts must be a non-empty list")
    provenance = OracleProvenance(
        source_artifacts=tuple(
            _local_existing_path(
                item,
                f"provenance.source_artifacts[{index}]",
                root,
            )
            for index, item in enumerate(raw_artifacts)
        ),
        test_source=_text(
            provenance_data.get("test_source"), "provenance.test_source"
        )
        or "",
        oracle_evidence=_string_list(
            provenance_data.get("oracle_evidence"),
            "provenance.oracle_evidence",
        ),
    )

    review_data = _mapping(data.get("review"), "review")
    review_status = _text(review_data.get("status"), "review.status") or ""
    if review_status not in REVIEW_STATUSES:
        raise ReportOracleError(
            f"review.status must be one of {', '.join(sorted(REVIEW_STATUSES))}"
        )
    reviewer = _text(review_data.get("reviewer"), "review.reviewer", optional=True)
    raw_reviewed_at = _text(
        review_data.get("reviewed_at"), "review.reviewed_at", optional=True
    )
    reviewed_at: date | None = None
    if raw_reviewed_at:
        try:
            reviewed_at = date.fromisoformat(raw_reviewed_at)
        except ValueError as error:
            raise ReportOracleError("review.reviewed_at must be an ISO date") from error
    if review_status in {"reviewed", "missing_oracle"} and (
        not reviewer or reviewed_at is None
    ):
        raise ReportOracleError(
            f"{review_status} review requires reviewer and reviewed_at"
        )
    review = OracleReview(
        status=review_status,
        reviewer=reviewer,
        reviewed_at=reviewed_at,
        notes=_text(review_data.get("notes"), "review.notes", optional=True),
    )

    missing_reason = _text(
        data.get("missing_oracle_reason"), "missing_oracle_reason", optional=True
    )
    has_executable = any(key in data for key in ("test", "oracle", "timeout_sec"))
    if missing_reason and has_executable:
        raise ReportOracleError(
            "missing_oracle_reason and executable fields are mutually exclusive"
        )
    if missing_reason:
        if review_status not in {"pending", "missing_oracle"}:
            raise ReportOracleError(
                "missing_oracle_reason requires a pending or missing_oracle review"
            )
        return ReportOracle(
            schema_version=1,
            report_id=report_id,
            dbms=dbms,
            issue_url=issue_url,
            found_version=found_version,
            protocol=protocol,
            test=None,
            oracle=None,
            timeout_sec=None,
            provenance=provenance,
            review=review,
            missing_oracle_reason=missing_reason,
            source_path=source_path,
        )

    if not all(key in data for key in ("test", "oracle", "timeout_sec")):
        raise ReportOracleError(
            "executable oracle requires test, oracle, and timeout_sec"
        )
    if review_status == "missing_oracle":
        raise ReportOracleError("missing_oracle review requires missing_oracle_reason")
    test_data = _mapping(data.get("test"), "test")
    test = TestProgram(
        setup=_string_list(test_data.get("setup"), "test.setup"),
        query=_text(test_data.get("query"), "test.query") or "",
        cleanup=_string_list(test_data.get("cleanup"), "test.cleanup"),
    )
    oracle_data = _mapping(data.get("oracle"), "oracle")
    oracle_type = _text(oracle_data.get("type"), "oracle.type") or ""
    if oracle_type not in ORACLE_TYPES:
        raise ReportOracleError(
            f"oracle.type must be one of {', '.join(ORACLE_TYPES)}"
        )
    if not provenance.oracle_evidence:
        raise ReportOracleError(
            "provenance.oracle_evidence must not be empty for executable oracle"
        )
    timeout = data.get("timeout_sec")
    if type(timeout) is not int or timeout <= 0:
        raise ReportOracleError("timeout_sec must be a positive integer")
    return ReportOracle(
        schema_version=1,
        report_id=report_id,
        dbms=dbms,
        issue_url=issue_url,
        found_version=found_version,
        protocol=protocol,
        test=test,
        oracle=OracleSpec(
            type=oracle_type,
            payload={key: value for key, value in oracle_data.items() if key != "type"},
        ),
        timeout_sec=timeout,
        provenance=provenance,
        review=review,
        missing_oracle_reason=None,
        source_path=source_path,
    )


def validate_report_oracles(
    oracles: Iterable[ReportOracle], registry_rows: Iterable[Mapping[str, object]]
) -> dict[str, int]:
    oracle_by_id: dict[str, ReportOracle] = {}
    for oracle in oracles:
        if oracle.report_id in oracle_by_id:
            raise ReportOracleError(f"duplicate report_id: {oracle.report_id}")
        oracle_by_id[oracle.report_id] = oracle
    registry: dict[str, tuple[str, str]] = {}
    for raw in registry_rows:
        report_id = str(raw.get("report_id") or "").strip()
        if not report_id:
            raise ReportOracleError("registry contains an empty report_id")
        if report_id in registry:
            raise ReportOracleError(f"duplicate registry report_id: {report_id}")
        registry[report_id] = (
            str(raw.get("dbms") or "").strip(),
            str(raw.get("issue_url") or "").strip(),
        )
    if set(oracle_by_id) != set(registry):
        missing = sorted(set(registry) - set(oracle_by_id))
        extra = sorted(set(oracle_by_id) - set(registry))
        raise ReportOracleError(
            f"population mismatch: missing={missing}, extra={extra}"
        )
    counts: dict[str, int] = {}
    for report_id, oracle in oracle_by_id.items():
        dbms, issue_url = registry[report_id]
        if oracle.dbms != dbms or oracle.issue_url.rstrip("/") != issue_url.rstrip("/"):
            raise ReportOracleError(f"{report_id}: registry identity mismatch")
        counts[dbms] = counts.get(dbms, 0) + 1
    return dict(sorted(counts.items()))
