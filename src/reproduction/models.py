from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any, Iterator


class OutcomeStatus(str, Enum):
    REPRODUCED = "REPRODUCED"
    NOT_REPRODUCED = "NOT_REPRODUCED"
    FIXED = "FIXED"
    UNSUPPORTED = "UNSUPPORTED"
    ENVIRONMENT_ERROR = "ENVIRONMENT_ERROR"
    INCONCLUSIVE = "INCONCLUSIVE"


@dataclass(frozen=True)
class IssueRef:
    url: str
    status: str
    report_path: Path


@dataclass(frozen=True)
class CaseTarget:
    id: str
    image: str | None = None


@dataclass(frozen=True)
class CaseTargets:
    buggy: CaseTarget
    fixed: tuple[CaseTarget, ...]


@dataclass(frozen=True)
class TestProgram:
    setup: tuple[str, ...]
    query: str
    cleanup: tuple[str, ...]


@dataclass(frozen=True)
class OracleSpec:
    type: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class ReproductionPolicy:
    attempts: int
    timeout_sec: int


@dataclass(frozen=True)
class Provenance:
    query_id: str | None
    case_id: str | None
    source_artifacts: tuple[Path, ...]
    skeleton_ids: tuple[str, ...]
    feature_ids: tuple[str, ...]


@dataclass(frozen=True)
class ReproductionCase:
    schema_version: int
    family_id: str
    title: str
    paper_set: str
    dbms: str
    symptom: str
    issue: IssueRef
    targets: CaseTargets
    test: TestProgram
    oracle: OracleSpec
    reproduction: ReproductionPolicy
    provenance: Provenance
    source_path: Path


@dataclass(frozen=True)
class ReportRecord:
    report_id: str
    dbms: str
    issue_url: str
    report_path: Path
    maintainer_status: str
    fix_status: str
    family_ids: tuple[str, ...]
    reproduction_scope: str
    reason: str | None = None

    def with_family_ids(self, family_ids: tuple[str, ...]) -> ReportRecord:
        return replace(self, family_ids=family_ids)


@dataclass(frozen=True)
class ClaimPartition:
    neo4j: int
    memgraph: int
    falkordb: int
    arcadedb: int
    apache_age: int
    total: int

    def __getitem__(self, dbms: str) -> int:
        if dbms not in {"neo4j", "memgraph", "falkordb", "arcadedb", "apache_age", "total"}:
            raise KeyError(dbms)
        return getattr(self, dbms)

    def __iter__(self) -> Iterator[str]:
        return iter(("neo4j", "memgraph", "falkordb", "arcadedb", "apache_age", "total"))

    def by_dbms(self) -> dict[str, int]:
        return {
            "neo4j": self.neo4j,
            "memgraph": self.memgraph,
            "falkordb": self.falkordb,
            "arcadedb": self.arcadedb,
            "apache_age": self.apache_age,
        }


@dataclass(frozen=True)
class PaperClaims:
    fixed_version_families: ClaimPartition
    retained_reports: ClaimPartition


@dataclass(frozen=True)
class TargetSpec:
    id: str
    dbms: str
    executor_target: str
    image: str
    require_resolved_image_id: bool = False
