from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from src.reproduction.audit import audit_catalog
from src.reproduction.models import (
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
    TestProgram as CaseProgram,
)


def _partition(
    *,
    neo4j: int = 0,
    memgraph: int = 0,
    falkordb: int = 0,
    arcadedb: int = 0,
    apache_age: int = 0,
) -> ClaimPartition:
    return ClaimPartition(
        neo4j=neo4j,
        memgraph=memgraph,
        falkordb=falkordb,
        arcadedb=arcadedb,
        apache_age=apache_age,
        total=neo4j + memgraph + falkordb + arcadedb + apache_age,
    )


def _claims(
    families: ClaimPartition | None = None,
    reports: ClaimPartition | None = None,
) -> PaperClaims:
    return PaperClaims(
        fixed_version_families=families or _partition(),
        retained_reports=reports or _partition(),
    )


@pytest.fixture
def sample_report_path(tmp_path: Path) -> Path:
    path = tmp_path / "reports" / "neo4j-001.md"
    path.parent.mkdir()
    path.write_text("# Retained report\n", encoding="utf-8")
    return path


@pytest.fixture
def sample_case(sample_report_path: Path, tmp_path: Path) -> ReproductionCase:
    return ReproductionCase(
        schema_version=1,
        family_id="HISTDB-NEO-001",
        title="Sample family",
        paper_set="fixed-version-50",
        dbms="neo4j",
        symptom="wrong result",
        issue=IssueRef(
            url="https://github.com/neo4j/neo4j/issues/1",
            status="fixed",
            report_path=sample_report_path,
        ),
        targets=CaseTargets(
            buggy=CaseTarget(id="neo4j-buggy", image="neo4j:buggy"),
            fixed=(CaseTarget(id="neo4j-fixed", image="neo4j:fixed"),),
        ),
        test=CaseProgram(setup=("CREATE (:N)",), query="MATCH (n) RETURN n", cleanup=()),
        oracle=OracleSpec(
            type="exact_rows",
            payload={"expected_buggy": [], "expected_correct": [], "ordered": True},
        ),
        reproduction=ReproductionPolicy(attempts=3, timeout_sec=10),
        provenance=Provenance(
            query_id=None,
            case_id=None,
            source_artifacts=(),
            skeleton_ids=(),
            feature_ids=(),
        ),
        source_path=tmp_path / "cases" / "neo4j" / "HISTDB-NEO-001.yaml",
    )


@pytest.fixture
def sample_report(sample_report_path: Path) -> ReportRecord:
    return ReportRecord(
        report_id="REPORT-NEO-001",
        dbms="neo4j",
        issue_url="https://github.com/neo4j/neo4j/issues/1",
        report_path=sample_report_path,
        maintainer_status="confirmed",
        fix_status="fixed",
        family_ids=("HISTDB-NEO-001",),
        reproduction_scope="paper_fixed_version",
    )


@pytest.fixture
def sample_claims() -> PaperClaims:
    return _claims(_partition(neo4j=1), _partition(neo4j=1))


def _codes(report: object) -> set[str]:
    return {item.code for item in report.errors}  # type: ignore[attr-defined]


def test_audit_accepts_complete_catalog_and_keeps_count_units_separate(
    sample_claims: PaperClaims,
    sample_case: ReproductionCase,
    sample_report: ReportRecord,
) -> None:
    audit = audit_catalog([sample_case], [sample_report], sample_claims)

    assert audit.ok
    payload = audit.to_dict()
    assert payload["counts"]["fixed_version_families"]["actual"] == {
        "neo4j": 1,
        "memgraph": 0,
        "falkordb": 0,
        "arcadedb": 0,
        "apache_age": 0,
        "total": 1,
    }
    assert payload["counts"]["retained_reports"]["actual"] == {
        "neo4j": 1,
        "memgraph": 0,
        "falkordb": 0,
        "arcadedb": 0,
        "apache_age": 0,
        "total": 1,
    }
    assert payload["errors"] == []


def test_audit_reports_per_dbms_and_total_mismatches_for_both_populations(
    sample_case: ReproductionCase,
    sample_report: ReportRecord,
) -> None:
    claims = _claims(_partition(memgraph=2), _partition(memgraph=2))

    audit = audit_catalog([sample_case], [sample_report], claims)

    assert not audit.ok
    family_subjects = {
        item.subject for item in audit.errors if item.code == "family_count_mismatch"
    }
    report_subjects = {
        item.subject for item in audit.errors if item.code == "report_count_mismatch"
    }
    assert family_subjects == {
        "fixed_version_families.neo4j",
        "fixed_version_families.memgraph",
        "fixed_version_families.total",
    }
    assert report_subjects == {
        "retained_reports.neo4j",
        "retained_reports.memgraph",
        "retained_reports.total",
    }


def test_audit_excludes_case_outside_fixed_version_paper_set(
    sample_case: ReproductionCase,
    sample_report: ReportRecord,
) -> None:
    noncanonical_case = replace(sample_case, paper_set="historical-only")
    claims = _claims(reports=_partition(neo4j=1))

    audit = audit_catalog([noncanonical_case], [sample_report], claims)

    assert "unexpected_paper_set" in _codes(audit)
    assert audit.to_dict()["counts"]["fixed_version_families"]["actual"] == {
        "neo4j": 0,
        "memgraph": 0,
        "falkordb": 0,
        "arcadedb": 0,
        "apache_age": 0,
        "total": 0,
    }
    assert "family_count_mismatch" not in _codes(audit)


def test_audit_rejects_paper_fixed_version_report_without_family_mapping(
    sample_report: ReportRecord,
) -> None:
    orphan = replace(sample_report, family_ids=(), reason="not executable")

    audit = audit_catalog([], [orphan], _claims(reports=_partition(neo4j=1)))

    assert "unmapped_report" in _codes(audit)


@pytest.mark.parametrize("scope", ["historical_version", "index_only"])
def test_audit_allows_reasoned_non_fixed_report_without_family_mapping(
    sample_report: ReportRecord,
    scope: str,
) -> None:
    report = replace(
        sample_report,
        family_ids=(),
        reproduction_scope=scope,
        reason="No executable fixed-version case is retained.",
    )

    audit = audit_catalog([], [report], _claims(reports=_partition(neo4j=1)))

    assert audit.ok, audit.format_errors()


@pytest.mark.parametrize("scope", ["historical_version", "index_only"])
def test_audit_requires_reason_for_unmapped_non_fixed_report(
    sample_report: ReportRecord,
    scope: str,
) -> None:
    report = replace(sample_report, family_ids=(), reproduction_scope=scope, reason=" ")

    audit = audit_catalog([], [report], _claims(reports=_partition(neo4j=1)))

    assert "unmapped_report" in _codes(audit)


def test_audit_checks_unknown_ids_and_dbms_for_mapped_non_fixed_report(
    sample_report: ReportRecord,
) -> None:
    report = replace(
        sample_report,
        family_ids=("HISTDB-MEM-999",),
        reproduction_scope="historical_version",
        reason=None,
    )

    audit = audit_catalog([], [report], _claims(reports=_partition(neo4j=1)))

    assert {"unknown_family_reference", "report_family_dbms_mismatch"} <= _codes(audit)


def test_audit_reports_duplicate_ids_and_issue_urls_deterministically(
    sample_case: ReproductionCase,
    sample_report: ReportRecord,
) -> None:
    cases = [sample_case, replace(sample_case, source_path=Path("duplicate.yaml"))]
    reports = [sample_report, replace(sample_report)]
    claims = _claims(_partition(neo4j=2), _partition(neo4j=2))

    forward = audit_catalog(cases, reports, claims)
    reverse = audit_catalog(list(reversed(cases)), list(reversed(reports)), claims)

    assert {
        "duplicate_family_id",
        "duplicate_report_id",
        "duplicate_issue_url",
    } <= _codes(forward)
    assert forward.to_dict() == reverse.to_dict()
    assert list(forward.errors) == sorted(
        forward.errors,
        key=lambda item: (item.code, item.subject, item.message),
    )


def test_audit_counts_unique_ids_with_deterministic_duplicate_selection(
    sample_case: ReproductionCase,
    sample_report: ReportRecord,
) -> None:
    neo4j_case = replace(sample_case, source_path=Path("z-neo4j.yaml"))
    memgraph_case = replace(
        sample_case,
        dbms="memgraph",
        source_path=Path("a-memgraph.yaml"),
    )
    neo4j_report = replace(sample_report, issue_url="https://example.test/issues/neo4j")
    memgraph_report = replace(
        sample_report,
        dbms="memgraph",
        issue_url="https://example.test/issues/memgraph",
    )
    claims = _claims(_partition(memgraph=2), _partition(memgraph=2))

    forward = audit_catalog(
        [neo4j_case, memgraph_case],
        [neo4j_report, memgraph_report],
        claims,
    )
    reverse = audit_catalog(
        [memgraph_case, neo4j_case],
        [memgraph_report, neo4j_report],
        claims,
    )

    assert forward.to_dict() == reverse.to_dict()
    assert forward.to_dict()["counts"]["fixed_version_families"]["actual"] == {
        "neo4j": 0,
        "memgraph": 1,
        "falkordb": 0,
        "arcadedb": 0,
        "apache_age": 0,
        "total": 1,
    }
    assert forward.to_dict()["counts"]["retained_reports"]["actual"] == {
        "neo4j": 0,
        "memgraph": 1,
        "falkordb": 0,
        "arcadedb": 0,
        "apache_age": 0,
        "total": 1,
    }
    assert {"duplicate_family_id", "duplicate_report_id"} <= _codes(forward)
    assert any(
        item.code == "family_count_mismatch"
        and item.subject == "fixed_version_families.total"
        for item in forward.errors
    )
    assert any(
        item.code == "report_count_mismatch"
        and item.subject == "retained_reports.total"
        for item in forward.errors
    )


def test_audit_reports_missing_report_file_and_url(
    sample_claims: PaperClaims,
    sample_case: ReproductionCase,
    sample_report: ReportRecord,
    tmp_path: Path,
) -> None:
    missing = replace(
        sample_report,
        issue_url=" ",
        report_path=tmp_path / "reports" / "missing.md",
    )

    audit = audit_catalog([sample_case], [missing], sample_claims)

    assert {"missing_issue_url", "missing_report_file"} <= _codes(audit)


def test_audit_reports_missing_case_issue_file_and_url(
    sample_claims: PaperClaims,
    sample_case: ReproductionCase,
    sample_report: ReportRecord,
    tmp_path: Path,
) -> None:
    invalid_case = replace(
        sample_case,
        issue=replace(
            sample_case.issue,
            url=" ",
            report_path=tmp_path / "case-reports" / "missing.md",
        ),
    )

    audit = audit_catalog([invalid_case], [sample_report], sample_claims)

    assert {"missing_case_issue_url", "missing_case_report_file"} <= _codes(audit)


def test_audit_reports_executable_family_without_linked_retained_report(
    sample_claims: PaperClaims,
    sample_case: ReproductionCase,
    sample_report: ReportRecord,
) -> None:
    unrelated = replace(
        sample_report,
        family_ids=(),
        reproduction_scope="index_only",
        reason="No fixed-version case retained.",
    )

    audit = audit_catalog([sample_case], [unrelated], sample_claims)

    assert "family_without_report" in _codes(audit)
    assert "unmapped_report" not in _codes(audit)


def test_audit_accumulates_missing_buggy_target_and_structured_oracle(
    sample_claims: PaperClaims,
    sample_case: ReproductionCase,
    sample_report: ReportRecord,
) -> None:
    invalid_case = replace(
        sample_case,
        targets=replace(sample_case.targets, buggy=CaseTarget(id="")),
        oracle=OracleSpec(type="", payload={}),
    )

    audit = audit_catalog([invalid_case], [sample_report], sample_claims)

    assert {"missing_buggy_target", "missing_structured_oracle"} <= _codes(audit)


def test_audit_reports_id_case_and_linked_report_dbms_disagreement(
    sample_case: ReproductionCase,
    sample_report: ReportRecord,
) -> None:
    mismatched_case = replace(sample_case, family_id="HISTDB-MEM-001")
    mismatched_report = replace(
        sample_report,
        dbms="falkordb",
        family_ids=("HISTDB-MEM-001",),
    )
    claims = _claims(_partition(neo4j=1), _partition(falkordb=1))

    audit = audit_catalog([mismatched_case], [mismatched_report], claims)

    assert {"family_id_dbms_mismatch", "report_family_dbms_mismatch"} <= _codes(audit)
    assert "unknown_family_reference" not in _codes(audit)
