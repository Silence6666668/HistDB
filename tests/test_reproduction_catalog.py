from pathlib import Path

import pytest

from src.reproduction.audit import audit_catalog
from src.reproduction.manifest import discover_cases, load_paper_claims, load_report_index


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.mark.catalog
def test_checked_in_catalog_matches_paper_claims(repo_root: Path) -> None:
    cases = discover_cases(repo_root / "reproduction" / "cases", repo_root)
    reports = load_report_index(repo_root / "reproduction" / "reports-148.yaml", repo_root)
    claims = load_paper_claims(repo_root / "reproduction" / "paper-claims.yaml")
    audit = audit_catalog(cases, reports, claims)
    assert audit.ok, audit.format_errors()

