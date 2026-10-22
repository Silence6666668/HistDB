from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from src.common.config import load_yaml
from src.reproduction.audit import AuditReport, audit_catalog
from src.reproduction.adapter import execute_with_stage4, probe_target_health
from src.reproduction.doctor import check_environment, pair_doctor_targets
from src.reproduction.manifest import (
    DBMS_NAMES,
    ManifestError,
    discover_cases,
    load_case,
    load_paper_claims,
    load_report_index,
    load_targets,
)
from src.reproduction.models import OutcomeStatus, ReproductionCase
from src.reproduction.reporting import write_run_artifacts
from src.reproduction.runner import (
    CaseRunResult,
    environment_error_result,
    missing_fixed_target_result,
    run_case,
)
from src.stage4.executor import DbTarget, load_db_targets


EXIT_OK = 0
EXIT_MANIFEST_ERROR = 2
EXIT_ENVIRONMENT_ERROR = 3
EXIT_REPRODUCTION_MISS = 4
EXIT_AUDIT_MISMATCH = 5

DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASES_ROOT = "reproduction/cases"
DEFAULT_REPORTS = "reproduction/reports-148.yaml"
DEFAULT_CLAIMS = "reproduction/paper-claims.yaml"
DEFAULT_TARGETS = "reproduction/targets.yaml"
DEFAULT_DB_CONFIG = "reproduction/db_targets.yaml"
DEFAULT_RUN_OUTPUT = "reproduction_runs/latest"


def _add_path_options(parser: argparse.ArgumentParser, *, with_defaults: bool) -> None:
    parser.add_argument(
        "--repo-root",
        default=DEFAULT_REPO_ROOT if with_defaults else argparse.SUPPRESS,
        help="repository root (default: detected project root)",
    )
    parser.add_argument(
        "--cases-root",
        default=DEFAULT_CASES_ROOT if with_defaults else argparse.SUPPRESS,
        help="case directory, relative to the repository root",
    )
    parser.add_argument(
        "--reports",
        default=DEFAULT_REPORTS if with_defaults else argparse.SUPPRESS,
        help="report index, relative to the repository root",
    )
    parser.add_argument(
        "--claims",
        default=DEFAULT_CLAIMS if with_defaults else argparse.SUPPRESS,
        help="paper claims manifest, relative to the repository root",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.cli.reproduce",
        description="Inspect and validate the static reproduction catalog.",
    )
    _add_path_options(parser, with_defaults=True)
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="list reproduction cases")
    _add_path_options(list_parser, with_defaults=False)
    list_parser.add_argument("--dbms", choices=DBMS_NAMES, help="filter by DBMS")
    list_parser.add_argument("--paper-set", help="filter by paper set")
    list_parser.add_argument("--format", choices=("table", "json"), default="table")
    list_parser.set_defaults(handler=_command_list)

    validate_parser = subparsers.add_parser("validate", help="validate catalog manifests")
    _add_path_options(validate_parser, with_defaults=False)
    validate_parser.add_argument("--case", help="validate one case manifest")
    validate_parser.add_argument("--dbms", choices=DBMS_NAMES, help="validate one DBMS partition")
    validate_parser.set_defaults(handler=_command_validate)

    audit_parser = subparsers.add_parser("audit", help="audit catalog paper claims")
    _add_path_options(audit_parser, with_defaults=False)
    audit_parser.add_argument("--format", choices=("table", "json"), default="table")
    audit_parser.set_defaults(handler=_command_audit)

    run_parser = subparsers.add_parser("run", help="execute reproduction cases")
    _add_path_options(run_parser, with_defaults=False)
    run_parser.add_argument("--case", help="family ID or case manifest path")
    run_parser.add_argument("--dbms", choices=DBMS_NAMES, help="select one DBMS")
    run_parser.add_argument("--paper-set", help="select one paper set")
    run_parser.add_argument("--target", choices=("buggy", "fixed"), default="buggy")
    run_parser.add_argument("--output-dir", default=DEFAULT_RUN_OUTPUT)
    run_parser.add_argument("--db-config", default=DEFAULT_DB_CONFIG)
    run_parser.add_argument(
        "--no-manage-target",
        action="store_true",
        help="connect only to configured endpoints",
    )
    run_parser.set_defaults(handler=_command_run)

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="inspect reproduction dependencies and targets without changing them",
    )
    _add_path_options(doctor_parser, with_defaults=False)
    doctor_parser.add_argument("--dbms", choices=DBMS_NAMES, help="check one DBMS")
    doctor_parser.add_argument("--format", choices=("table", "json"), default="table")
    doctor_parser.add_argument("--db-config", default=DEFAULT_DB_CONFIG)
    doctor_parser.set_defaults(handler=_command_doctor)

    return parser


def _repo_root(args: argparse.Namespace) -> Path:
    return Path(args.repo_root).resolve()


def _catalog_path(value: str | Path, repo_root: Path, field: str) -> Path:
    path = Path(value)
    try:
        resolved = (path if path.is_absolute() else repo_root / path).resolve()
    except (OSError, RuntimeError) as exc:
        raise ManifestError(f"cannot resolve {field} {value}") from exc
    try:
        resolved.relative_to(repo_root)
    except ValueError as exc:
        raise ManifestError(f"{field} must resolve beneath repo_root: {value}") from exc
    return resolved


def _cases(args: argparse.Namespace, repo_root: Path) -> list[ReproductionCase]:
    cases_root = _catalog_path(args.cases_root, repo_root, "case discovery root")
    if not cases_root.is_dir():
        raise ManifestError(
            f"cannot read case discovery root {cases_root}: directory does not exist"
        )
    return discover_cases(cases_root, repo_root=repo_root)


def _case_summary(case: ReproductionCase, repo_root: Path) -> dict[str, str]:
    return {
        "dbms": case.dbms,
        "family_id": case.family_id,
        "paper_set": case.paper_set,
        "source": case.source_path.relative_to(repo_root).as_posix(),
        "title": case.title,
    }


def _json_text(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True)


def _format_table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
    string_rows = [[str(value) for value in row] for row in rows]
    widths = [len(header) for header in headers]
    for row in string_rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))

    def render(row: Sequence[str]) -> str:
        return "  ".join(
            value.ljust(widths[index]) for index, value in enumerate(row)
        ).rstrip()

    separator = tuple("-" * width for width in widths)
    return "\n".join((render(headers), render(separator), *(render(row) for row in string_rows)))


def _command_list(args: argparse.Namespace) -> int:
    repo_root = _repo_root(args)
    cases = _cases(args, repo_root)
    selected = sorted(
        (
            case
            for case in cases
            if (args.dbms is None or case.dbms == args.dbms)
            and (args.paper_set is None or case.paper_set == args.paper_set)
        ),
        key=lambda case: (case.family_id, case.dbms, str(case.source_path)),
    )

    if args.format == "json":
        print(
            _json_text(
                {
                    "cases": [_case_summary(case, repo_root) for case in selected],
                    "count": len(selected),
                }
            )
        )
    else:
        print(
            _format_table(
                ("FAMILY_ID", "DBMS", "PAPER_SET", "TITLE"),
                [
                    (case.family_id, case.dbms, case.paper_set, case.title)
                    for case in selected
                ],
            )
        )
    return EXIT_OK


def _command_validate(args: argparse.Namespace) -> int:
    repo_root = _repo_root(args)
    if args.case and args.dbms:
        raise ManifestError("validate accepts either --case or --dbms, not both")
    if args.case:
        case_path = _catalog_path(args.case, repo_root, "case path")
        case = load_case(case_path, repo_root=repo_root)
        print(f"Validated case {case.family_id}.")
        return EXIT_OK

    cases = _cases(args, repo_root)
    if args.dbms:
        cases = [case for case in cases if case.dbms == args.dbms]
        if not cases:
            raise ManifestError(f"validate matched no {args.dbms} cases")
    reports = load_report_index(
        _catalog_path(args.reports, repo_root, "reports manifest path"),
        repo_root=repo_root,
    )
    load_paper_claims(_catalog_path(args.claims, repo_root, "claims manifest path"))
    if args.dbms:
        noun = "case" if len(cases) == 1 else "cases"
        print(
            f"Validated {len(cases)} {args.dbms} {noun}, "
            f"{len(reports)} reports, and paper claims."
        )
    else:
        print(f"Validated {len(cases)} cases, {len(reports)} reports, and paper claims.")
    return EXIT_OK


def _audit_table(report: AuditReport) -> str:
    rows: list[tuple[object, ...]] = []
    populations = (
        ("fixed_version_families", report.family_counts, report.expected_family_counts),
        ("retained_reports", report.report_counts, report.expected_report_counts),
    )
    for population, actual, expected in populations:
        for dbms in (*DBMS_NAMES, "total"):
            rows.append(
                (
                    population,
                    dbms,
                    actual[dbms],
                    expected[dbms],
                    "yes" if actual[dbms] == expected[dbms] else "no",
                )
            )
    table = _format_table(
        ("POPULATION", "DBMS", "ACTUAL", "EXPECTED", "MATCH"),
        rows,
    )
    status = "Catalog audit passed." if report.ok else "Catalog audit failed."
    if report.ok:
        return f"{status}\n{table}"
    return f"{status}\n{table}\n\n{report.format_errors()}"


def _command_audit(args: argparse.Namespace) -> int:
    repo_root = _repo_root(args)
    cases = _cases(args, repo_root)
    reports = load_report_index(
        _catalog_path(args.reports, repo_root, "reports manifest path"),
        repo_root=repo_root,
    )
    claims = load_paper_claims(
        _catalog_path(args.claims, repo_root, "claims manifest path")
    )
    report = audit_catalog(cases, reports, claims)

    if args.format == "json":
        print(_json_text(report.to_dict()))
    else:
        print(_audit_table(report))
    return EXIT_OK if report.ok else EXIT_AUDIT_MISMATCH


def _selected_run_cases(
    args: argparse.Namespace,
    repo_root: Path,
) -> list[ReproductionCase]:
    selectors = [args.case is not None, args.dbms is not None, args.paper_set is not None]
    if sum(selectors) != 1:
        raise ManifestError("run requires exactly one selector: --case, --dbms, or --paper-set")

    cases = _cases(args, repo_root)
    if args.case is not None:
        by_id = [case for case in cases if case.family_id == args.case]
        if by_id:
            return by_id
        case_path = _catalog_path(args.case, repo_root, "case path")
        return [load_case(case_path, repo_root=repo_root)]
    if args.dbms is not None:
        selected = [case for case in cases if case.dbms == args.dbms]
    else:
        selected = [case for case in cases if case.paper_set == args.paper_set]
    if not selected:
        raise ManifestError("run selector matched no reproduction cases")
    return sorted(selected, key=lambda case: (case.family_id, str(case.source_path)))


def _load_runtime_targets(path: Path) -> list[DbTarget]:
    try:
        config = load_yaml(path)
    except (OSError, ValueError) as exc:
        raise ManifestError(f"cannot load DB configuration {path}: {exc}") from exc
    targets = load_db_targets(config)
    if not targets:
        raise ManifestError(f"DB configuration has no enabled targets: {path}")
    return targets


def _target_for_case(
    case: ReproductionCase,
    role: str,
    target_specs: dict[str, Any],
    runtime_targets: dict[str, DbTarget],
) -> tuple[Any | None, DbTarget | None, CaseRunResult | None]:
    if role == "buggy":
        case_target = case.targets.buggy
    elif not case.targets.fixed:
        return None, None, missing_fixed_target_result(case)
    else:
        case_target = case.targets.fixed[0]

    spec = target_specs.get(case_target.id)
    if spec is None:
        return (
            None,
            None,
            environment_error_result(
                case,
                target_role=role,
                target_id=case_target.id,
                target_tag=case_target.image,
                explanation=f"target manifest has no entry for {case_target.id}",
            ),
        )
    if spec.dbms != case.dbms:
        return (
            spec,
            None,
            environment_error_result(
                case,
                target_role=role,
                target_id=case_target.id,
                target_tag=case_target.image or spec.image,
                explanation=(
                    f"target {case_target.id} declares DBMS {spec.dbms}, "
                    f"but case requires {case.dbms}"
                ),
            ),
        )
    runtime_target = runtime_targets.get(spec.executor_target)
    if runtime_target is None:
        return (
            spec,
            None,
            environment_error_result(
                case,
                target_role=role,
                target_id=case_target.id,
                target_tag=case_target.image or spec.image,
                explanation=(
                    f"DB configuration has no enabled executor target "
                    f"{spec.executor_target}"
                ),
            ),
        )
    return spec, runtime_target, None


def _run_exit_code(results: Sequence[CaseRunResult]) -> int:
    if any(result.status is OutcomeStatus.ENVIRONMENT_ERROR for result in results):
        return EXIT_ENVIRONMENT_ERROR
    for result in results:
        expected = (
            OutcomeStatus.REPRODUCED
            if result.target_role == "buggy"
            else OutcomeStatus.FIXED
        )
        if result.status is not expected:
            return EXIT_REPRODUCTION_MISS
    return EXIT_OK


def _command_run(args: argparse.Namespace) -> int:
    repo_root = _repo_root(args)
    cases = _selected_run_cases(args, repo_root)
    target_manifest_path = _catalog_path(DEFAULT_TARGETS, repo_root, "target manifest path")
    db_config_path = _catalog_path(args.db_config, repo_root, "DB configuration path")
    target_specs = load_targets(target_manifest_path)
    runtime_targets = {
        target.name: target for target in _load_runtime_targets(db_config_path)
    }

    results: list[CaseRunResult] = []
    for case in cases:
        spec, runtime_target, failure = _target_for_case(
            case,
            args.target,
            target_specs,
            runtime_targets,
        )
        if failure is not None:
            results.append(failure)
            continue
        assert spec is not None and runtime_target is not None
        results.append(
            run_case(
                case,
                runtime_target,
                lambda selected_case, selected_target: execute_with_stage4(
                    selected_case,
                    selected_target,
                    target_role=args.target,
                ),
                probe_target_health,
                target_role=args.target,
                target_id=spec.id,
                target_tag=case.targets.buggy.image or spec.image
                if args.target == "buggy"
                else case.targets.fixed[0].image or spec.image,
            )
        )

    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = repo_root / output_dir
    paths = write_run_artifacts(results, output_dir)
    print(f"Wrote {len(results)} case-target result(s) to {paths['summary.json'].parent}.")
    return _run_exit_code(results)


def _command_doctor(args: argparse.Namespace) -> int:
    repo_root = _repo_root(args)
    target_manifest_path = _catalog_path(DEFAULT_TARGETS, repo_root, "target manifest path")
    db_config_path = _catalog_path(args.db_config, repo_root, "DB configuration path")
    target_specs = load_targets(target_manifest_path)
    runtime_targets = _load_runtime_targets(db_config_path)
    targets = pair_doctor_targets(target_specs, runtime_targets, dbms=args.dbms)
    if not targets:
        raise ManifestError("doctor selection matched no targets")
    report = check_environment(targets)

    if args.format == "json":
        print(_json_text(report.to_dict()))
    else:
        print(
            _format_table(
                ("CHECK", "TARGET", "STATUS", "MESSAGE"),
                [
                    (
                        check.code,
                        check.target_id or "-",
                        check.status,
                        check.message,
                    )
                    for check in report.checks
                ],
            )
        )
    return EXIT_OK if report.ok else EXIT_ENVIRONMENT_ERROR


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except ManifestError as exc:
        print(f"manifest error: {exc}", file=sys.stderr)
        return EXIT_MANIFEST_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
