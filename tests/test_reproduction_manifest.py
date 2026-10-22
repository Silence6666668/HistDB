from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from src.reproduction.manifest import (
    ManifestError,
    discover_cases,
    load_case,
    load_paper_claims,
    load_report_index,
    load_targets,
)
from src.reproduction.models import OutcomeStatus


def _case_yaml(*, family_id: str = "HISTDB-MEM-001", report_path: str = "issue_reports/case.md") -> str:
    return f"""
schema_version: 1
family_id: {family_id}
title: Example
paper_set: fixed-version-50
dbms: memgraph
symptom: wrong_result
issue:
  url: https://github.com/memgraph/memgraph/issues/1
  status: confirmed
  report_path: {report_path}
targets:
  buggy: {{id: memgraph-3.9.0}}
  fixed: []
test:
  setup: []
  query: RETURN 1 AS x
  cleanup: []
oracle:
  type: scalar_equals
  field: x
  expected_buggy: 2
  expected_correct: 1
reproduction: {{attempts: 3, timeout_sec: 10}}
provenance: {{source_artifacts: [], skeleton_ids: [], feature_ids: []}}
""".strip()


def test_load_case_resolves_report_path_and_preserves_multiline_query(tmp_path: Path) -> None:
    report = tmp_path / "issue_reports" / "case.md"
    report.parent.mkdir()
    report.write_text("report", encoding="utf-8")
    case = tmp_path / "case.yaml"
    case.write_text(
        """
schema_version: 1
family_id: HISTDB-MEM-001
title: Example
paper_set: fixed-version-50
dbms: memgraph
symptom: wrong_result
issue:
  url: https://github.com/memgraph/memgraph/issues/1
  status: confirmed
  report_path: issue_reports/case.md
targets:
  buggy: {id: memgraph-3.9.0}
  fixed: []
test:
  setup: []
  query: |-
    RETURN 1 AS x
  cleanup: []
oracle:
  type: scalar_equals
  field: x
  expected_buggy: 2
  expected_correct: 1
reproduction: {attempts: 3, timeout_sec: 10}
provenance: {source_artifacts: [], skeleton_ids: [], feature_ids: []}
""".strip(),
        encoding="utf-8",
    )

    loaded = load_case(case, repo_root=tmp_path)

    assert loaded.family_id == "HISTDB-MEM-001"
    assert loaded.test.query == "RETURN 1 AS x"
    assert loaded.issue.report_path == report.resolve()


def test_load_case_rejects_unknown_oracle_type(tmp_path: Path) -> None:
    case = tmp_path / "case.yaml"
    case.write_text("schema_version: 1\nfamily_id: X\noracle: {type: guessed}\n", encoding="utf-8")

    with pytest.raises(ManifestError, match="oracle.type"):
        load_case(case, repo_root=tmp_path)


def test_paper_claim_totals_must_equal_partition_sum(tmp_path: Path) -> None:
    claims = tmp_path / "claims.yaml"
    claims.write_text(
        "fixed_version_families: {neo4j: 3, memgraph: 13, falkordb: 11, arcadedb: 20, apache_age: 3, total: 49}\n"
        "retained_reports: {neo4j: 14, memgraph: 26, falkordb: 40, arcadedb: 53, apache_age: 15, total: 148}\n",
        encoding="utf-8",
    )

    with pytest.raises(ManifestError, match="fixed_version_families.total"):
        load_paper_claims(claims)


def test_outcome_status_has_one_shared_six_value_contract() -> None:
    assert [status.value for status in OutcomeStatus] == [
        "REPRODUCED",
        "NOT_REPRODUCED",
        "FIXED",
        "UNSUPPORTED",
        "ENVIRONMENT_ERROR",
        "INCONCLUSIVE",
    ]


def test_case_models_are_frozen_and_oracle_payload_excludes_type(tmp_path: Path) -> None:
    case_path = tmp_path / "case.yaml"
    case_path.write_text(_case_yaml(), encoding="utf-8")

    loaded = load_case(case_path, repo_root=tmp_path)

    assert loaded.oracle.type == "scalar_equals"
    assert loaded.oracle.payload == {"field": "x", "expected_buggy": 2, "expected_correct": 1}
    with pytest.raises(FrozenInstanceError):
        loaded.title = "Changed"  # type: ignore[misc]


@pytest.mark.parametrize(("field", "value"), [("attempts", 0), ("timeout_sec", -1)])
def test_load_case_rejects_non_positive_reproduction_values(tmp_path: Path, field: str, value: int) -> None:
    case_path = tmp_path / "case.yaml"
    case_path.write_text(
        _case_yaml().replace(
            "reproduction: {attempts: 3, timeout_sec: 10}",
            f"reproduction: {{attempts: {value if field == 'attempts' else 3}, "
            f"timeout_sec: {value if field == 'timeout_sec' else 10}}}",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ManifestError, match=f"reproduction.{field}"):
        load_case(case_path, repo_root=tmp_path)


def test_load_case_rejects_evidence_path_outside_repo_root(tmp_path: Path) -> None:
    case_path = tmp_path / "case.yaml"
    case_path.write_text(_case_yaml(report_path="../outside.md"), encoding="utf-8")

    with pytest.raises(ManifestError, match="issue.report_path"):
        load_case(case_path, repo_root=tmp_path)


def test_load_case_rejects_case_path_outside_repo_root(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    external_case = tmp_path / "external-case.yaml"
    external_case.write_text(_case_yaml(), encoding="utf-8")

    with pytest.raises(ManifestError, match="case path"):
        load_case(external_case, repo_root=repo_root)


def test_discover_cases_rejects_root_outside_repo_root(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    external_root = tmp_path / "external-cases"
    external_root.mkdir()

    with pytest.raises(ManifestError, match="case discovery root"):
        discover_cases(external_root, repo_root=repo_root)


def test_discover_cases_returns_loaded_cases_in_path_order(tmp_path: Path) -> None:
    cases_root = tmp_path / "cases"
    cases_root.mkdir()
    (cases_root / "b.yaml").write_text(_case_yaml(family_id="HISTDB-MEM-002"), encoding="utf-8")
    (cases_root / "a.yaml").write_text(_case_yaml(family_id="HISTDB-MEM-001"), encoding="utf-8")

    loaded = discover_cases(cases_root, repo_root=tmp_path)

    assert [case.family_id for case in loaded] == ["HISTDB-MEM-001", "HISTDB-MEM-002"]


def test_load_report_index_resolves_local_report_paths(tmp_path: Path) -> None:
    index = tmp_path / "reports.yaml"
    index.write_text(
        """
reports:
  - report_id: MEM-001
    dbms: memgraph
    issue_url: https://github.com/memgraph/memgraph/issues/1
    report_path: issue_reports/case.md
    maintainer_status: confirmed
    fix_status: open
    family_ids: [HISTDB-MEM-001]
    reproduction_scope: paper_fixed_version
""".strip(),
        encoding="utf-8",
    )

    reports = load_report_index(index, repo_root=tmp_path)

    assert reports[0].report_id == "MEM-001"
    assert reports[0].family_ids == ("HISTDB-MEM-001",)
    assert reports[0].report_path == (tmp_path / "issue_reports" / "case.md").resolve()


def test_load_report_index_rejects_invalid_family_id(tmp_path: Path) -> None:
    index = tmp_path / "reports.yaml"
    index.write_text(
        """
reports:
  - report_id: MEM-001
    dbms: memgraph
    issue_url: https://github.com/memgraph/memgraph/issues/1
    report_path: issue_reports/case.md
    maintainer_status: confirmed
    fix_status: open
    family_ids: [X]
    reproduction_scope: paper_fixed_version
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ManifestError, match=r"reports\[0\]\.family_ids\[0\]"):
        load_report_index(index, repo_root=tmp_path)


def test_load_targets_returns_safe_typed_metadata(tmp_path: Path) -> None:
    targets_path = tmp_path / "targets.yaml"
    targets_path.write_text(
        """
targets:
  apache-age-1.7.0:
    dbms: apache_age
    executor_target: repro_age
    image: apache/age:latest
    require_resolved_image_id: true
""".strip(),
        encoding="utf-8",
    )

    targets = load_targets(targets_path)

    assert targets["apache-age-1.7.0"].executor_target == "repro_age"
    assert targets["apache-age-1.7.0"].require_resolved_image_id is True
    assert not hasattr(targets["apache-age-1.7.0"], "password")
