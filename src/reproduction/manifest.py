from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from .models import (
    CaseTarget,
    CaseTargets,
    ClaimPartition,
    IssueRef,
    OracleSpec,
    PaperClaims,
    Provenance,
    ReportRecord,
    ReproductionCase,
    ReproductionPolicy,
    TargetSpec,
    TestProgram,
)


DBMS_NAMES = ("neo4j", "memgraph", "falkordb", "arcadedb", "apache_age")
ORACLE_TYPES = (
    "exact_rows",
    "row_count",
    "scalar_equals",
    "error_matches",
    "timeout",
    "service_crash",
    "differential",
    "custom",
)
REPRODUCTION_SCOPES = ("paper_fixed_version", "historical_version", "index_only")
FAMILY_ID_RE = re.compile(r"^HISTDB-(NEO|MEM|FAL|ARC|AGE)-\d{3}$")
_MISSING = object()


class ManifestError(ValueError):
    """A reproduction manifest is malformed or violates its schema."""


def _load_yaml(path: str | Path) -> Any:
    manifest_path = Path(path)
    try:
        return yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ManifestError(f"cannot read manifest {manifest_path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ManifestError(f"invalid YAML in {manifest_path}: {exc}") from exc


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ManifestError(f"{field} must be a mapping")
    return value


def _required_mapping(parent: dict[str, Any], key: str, field: str | None = None) -> dict[str, Any]:
    return _mapping(parent.get(key, _MISSING), field or key)


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{field} must be a non-empty string")
    return value


def _required_string(parent: dict[str, Any], key: str, field: str | None = None) -> str:
    return _string(parent.get(key, _MISSING), field or key)


def _optional_string(parent: dict[str, Any], key: str, field: str) -> str | None:
    value = parent.get(key)
    if value is None:
        return None
    return _string(value, field)


def _integer(value: Any, field: str) -> int:
    if type(value) is not int:
        raise ManifestError(f"{field} must be an integer")
    return value


def _positive_integer(value: Any, field: str) -> int:
    parsed = _integer(value, field)
    if parsed <= 0:
        raise ManifestError(f"{field} must be positive")
    return parsed


def _nonnegative_integer(value: Any, field: str) -> int:
    parsed = _integer(value, field)
    if parsed < 0:
        raise ManifestError(f"{field} must be non-negative")
    return parsed


def _string_tuple(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ManifestError(f"{field} must be a list")
    return tuple(_string(item, f"{field}[{index}]") for index, item in enumerate(value))


def _family_id(value: Any, field: str) -> str:
    family_id = _string(value, field)
    if not FAMILY_ID_RE.fullmatch(family_id):
        raise ManifestError(f"{field} must match HISTDB-(NEO|MEM|FAL|ARC|AGE)-NNN")
    return family_id


def _family_id_tuple(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ManifestError(f"{field} must be a list")
    return tuple(_family_id(item, f"{field}[{index}]") for index, item in enumerate(value))


def _path_beneath_repo_root(value: str | Path, field: str, repo_root: str | Path) -> Path:
    root = Path(repo_root).resolve()
    candidate = Path(value)
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ManifestError(f"{field} must resolve beneath repo_root: {value}") from exc
    return resolved


def _local_path(value: Any, field: str, repo_root: str | Path) -> Path:
    return _path_beneath_repo_root(_string(value, field), field, repo_root)


def _case_target(value: Any, field: str) -> CaseTarget:
    data = _mapping(value, field)
    return CaseTarget(
        id=_required_string(data, "id", f"{field}.id"),
        image=_optional_string(data, "image", f"{field}.image"),
    )


def load_case(path: str | Path, repo_root: str | Path) -> ReproductionCase:
    source_path = _path_beneath_repo_root(path, "case path", repo_root)
    data = _mapping(_load_yaml(source_path), "case")

    schema_version = _integer(data.get("schema_version", _MISSING), "schema_version")
    if schema_version != 1:
        raise ManifestError("schema_version must be 1")

    oracle_data = _required_mapping(data, "oracle")
    oracle_type = _required_string(oracle_data, "type", "oracle.type")
    if oracle_type not in ORACLE_TYPES:
        raise ManifestError(f"oracle.type must be one of {', '.join(ORACLE_TYPES)}")
    oracle = OracleSpec(
        type=oracle_type,
        payload={key: value for key, value in oracle_data.items() if key != "type"},
    )

    family_id = _family_id(data.get("family_id", _MISSING), "family_id")

    dbms = _required_string(data, "dbms")
    if dbms not in DBMS_NAMES:
        raise ManifestError(f"dbms must be one of {', '.join(DBMS_NAMES)}")

    issue_data = _required_mapping(data, "issue")
    issue = IssueRef(
        url=_required_string(issue_data, "url", "issue.url"),
        status=_required_string(issue_data, "status", "issue.status"),
        report_path=_local_path(issue_data.get("report_path", _MISSING), "issue.report_path", repo_root),
    )

    targets_data = _required_mapping(data, "targets")
    fixed_data = targets_data.get("fixed", _MISSING)
    if not isinstance(fixed_data, list):
        raise ManifestError("targets.fixed must be a list")
    targets = CaseTargets(
        buggy=_case_target(targets_data.get("buggy", _MISSING), "targets.buggy"),
        fixed=tuple(_case_target(item, f"targets.fixed[{index}]") for index, item in enumerate(fixed_data)),
    )

    test_data = _required_mapping(data, "test")
    test = TestProgram(
        setup=_string_tuple(test_data.get("setup", _MISSING), "test.setup"),
        query=_required_string(test_data, "query", "test.query"),
        cleanup=_string_tuple(test_data.get("cleanup", _MISSING), "test.cleanup"),
    )

    reproduction_data = _required_mapping(data, "reproduction")
    reproduction = ReproductionPolicy(
        attempts=_positive_integer(reproduction_data.get("attempts", _MISSING), "reproduction.attempts"),
        timeout_sec=_positive_integer(
            reproduction_data.get("timeout_sec", _MISSING), "reproduction.timeout_sec"
        ),
    )

    provenance_data = _required_mapping(data, "provenance")
    source_artifact_values = provenance_data.get("source_artifacts", _MISSING)
    if not isinstance(source_artifact_values, list):
        raise ManifestError("provenance.source_artifacts must be a list")
    provenance = Provenance(
        query_id=_optional_string(provenance_data, "query_id", "provenance.query_id"),
        case_id=_optional_string(provenance_data, "case_id", "provenance.case_id"),
        source_artifacts=tuple(
            _local_path(item, f"provenance.source_artifacts[{index}]", repo_root)
            for index, item in enumerate(source_artifact_values)
        ),
        skeleton_ids=_string_tuple(
            provenance_data.get("skeleton_ids", _MISSING), "provenance.skeleton_ids"
        ),
        feature_ids=_string_tuple(provenance_data.get("feature_ids", _MISSING), "provenance.feature_ids"),
    )

    return ReproductionCase(
        schema_version=schema_version,
        family_id=family_id,
        title=_required_string(data, "title"),
        paper_set=_required_string(data, "paper_set"),
        dbms=dbms,
        symptom=_required_string(data, "symptom"),
        issue=issue,
        targets=targets,
        test=test,
        oracle=oracle,
        reproduction=reproduction,
        provenance=provenance,
        source_path=source_path,
    )


def discover_cases(root: str | Path, repo_root: str | Path) -> list[ReproductionCase]:
    cases_root = _path_beneath_repo_root(root, "case discovery root", repo_root)
    paths = sorted({*cases_root.rglob("*.yaml"), *cases_root.rglob("*.yml")})
    return [load_case(path, repo_root=repo_root) for path in paths]


def load_report_index(path: str | Path, repo_root: str | Path) -> list[ReportRecord]:
    data = _mapping(_load_yaml(path), "report index")
    report_values = data.get("reports", _MISSING)
    if not isinstance(report_values, list):
        raise ManifestError("reports must be a list")

    reports: list[ReportRecord] = []
    for index, value in enumerate(report_values):
        field = f"reports[{index}]"
        report = _mapping(value, field)
        dbms = _required_string(report, "dbms", f"{field}.dbms")
        if dbms not in DBMS_NAMES:
            raise ManifestError(f"{field}.dbms must be one of {', '.join(DBMS_NAMES)}")
        scope = _required_string(report, "reproduction_scope", f"{field}.reproduction_scope")
        if scope not in REPRODUCTION_SCOPES:
            raise ManifestError(
                f"{field}.reproduction_scope must be one of {', '.join(REPRODUCTION_SCOPES)}"
            )
        reports.append(
            ReportRecord(
                report_id=_required_string(report, "report_id", f"{field}.report_id"),
                dbms=dbms,
                issue_url=_required_string(report, "issue_url", f"{field}.issue_url"),
                report_path=_local_path(
                    report.get("report_path", _MISSING), f"{field}.report_path", repo_root
                ),
                maintainer_status=_required_string(
                    report, "maintainer_status", f"{field}.maintainer_status"
                ),
                fix_status=_required_string(report, "fix_status", f"{field}.fix_status"),
                family_ids=_family_id_tuple(
                    report.get("family_ids", _MISSING), f"{field}.family_ids"
                ),
                reproduction_scope=scope,
                reason=_optional_string(report, "reason", f"{field}.reason"),
            )
        )
    return reports


def _claim_partition(data: dict[str, Any], field: str) -> ClaimPartition:
    partition = _required_mapping(data, field)
    expected_keys = {*DBMS_NAMES, "total"}
    actual_keys = set(partition)
    if actual_keys != expected_keys:
        missing = sorted(expected_keys - actual_keys)
        unknown = sorted(actual_keys - expected_keys)
        details = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if unknown:
            details.append(f"unknown {', '.join(unknown)}")
        raise ManifestError(f"{field} has invalid keys: {'; '.join(details)}")

    counts = {
        key: _nonnegative_integer(partition[key], f"{field}.{key}") for key in expected_keys
    }
    partition_sum = sum(counts[dbms] for dbms in DBMS_NAMES)
    if counts["total"] != partition_sum:
        raise ManifestError(f"{field}.total must equal partition sum {partition_sum}")
    return ClaimPartition(**counts)


def load_paper_claims(path: str | Path) -> PaperClaims:
    data = _mapping(_load_yaml(path), "paper claims")
    return PaperClaims(
        fixed_version_families=_claim_partition(data, "fixed_version_families"),
        retained_reports=_claim_partition(data, "retained_reports"),
    )


def load_targets(path: str | Path) -> dict[str, TargetSpec]:
    data = _mapping(_load_yaml(path), "target manifest")
    target_values = _required_mapping(data, "targets")
    targets: dict[str, TargetSpec] = {}
    for target_id, value in target_values.items():
        target_name = _string(target_id, "targets key")
        field = f"targets.{target_name}"
        target = _mapping(value, field)
        dbms = _required_string(target, "dbms", f"{field}.dbms")
        if dbms not in DBMS_NAMES:
            raise ManifestError(f"{field}.dbms must be one of {', '.join(DBMS_NAMES)}")
        require_image_id = target.get("require_resolved_image_id", False)
        if type(require_image_id) is not bool:
            raise ManifestError(f"{field}.require_resolved_image_id must be a boolean")
        targets[target_name] = TargetSpec(
            id=target_name,
            dbms=dbms,
            executor_target=_required_string(target, "executor_target", f"{field}.executor_target"),
            image=_required_string(target, "image", f"{field}.image"),
            require_resolved_image_id=require_image_id,
        )
    return targets
