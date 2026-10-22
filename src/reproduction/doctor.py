from __future__ import annotations

import importlib.util
import os
import socket
import subprocess
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from src.stage4.executor import DbTarget

from .models import TargetSpec


CommandRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]
EndpointProbe = Callable[[DbTarget], bool]


@dataclass(frozen=True)
class DoctorTarget:
    spec: TargetSpec
    runtime: DbTarget


@dataclass(frozen=True)
class DoctorCheck:
    code: str
    status: str
    message: str
    target_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "status": self.status,
            "message": self.message,
            "target_id": self.target_id,
        }


@dataclass(frozen=True)
class DoctorTargetResult:
    target_id: str
    dbms: str
    executor_target: str
    image: str
    resolved_image_id: str | None
    endpoint_reachable: bool
    credential_env: str | None
    credential_present: bool | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "dbms": self.dbms,
            "executor_target": self.executor_target,
            "image": self.image,
            "resolved_image_id": self.resolved_image_id,
            "endpoint_reachable": self.endpoint_reachable,
            "credential_env": self.credential_env,
            "credential_present": self.credential_present,
        }


@dataclass(frozen=True)
class DoctorReport:
    checks: tuple[DoctorCheck, ...]
    targets: tuple[DoctorTargetResult, ...]

    @property
    def ok(self) -> bool:
        return not any(check.status == "error" for check in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checks": [check.to_dict() for check in self.checks],
            "targets": [target.to_dict() for target in self.targets],
        }


def _run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )


def _command_available(command: list[str], runner: CommandRunner) -> tuple[bool, str]:
    try:
        completed = runner(command)
    except (FileNotFoundError, OSError) as exc:
        return False, str(exc)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "command failed").strip()
        return False, detail
    return True, completed.stdout.strip()


def resolve_docker_identity(
    service: str,
    command_runner: CommandRunner = _run_command,
) -> str | None:
    command = ["docker", "image", "inspect", "--format", "{{.Id}}", service]
    available, output = _command_available(command, command_runner)
    if not available:
        return None
    identity = output.splitlines()[0].strip() if output else ""
    return identity or None


def _endpoint(target: DbTarget) -> tuple[str, int] | None:
    host = target.host
    port = target.port
    if (not host or not port) and target.uri:
        parsed = urlparse(target.uri)
        host = host or parsed.hostname
        port = port or parsed.port
    if not host or not port:
        return None
    return host, int(port)


def _probe_endpoint(target: DbTarget) -> bool:
    endpoint = _endpoint(target)
    if endpoint is None:
        return True
    try:
        with socket.create_connection(endpoint, timeout=0.5):
            return True
    except OSError:
        return False


def _dependency_checks() -> list[DoctorCheck]:
    dependencies = {
        "yaml": "PyYAML",
        "neo4j": "neo4j",
        "psycopg": "psycopg",
    }
    checks: list[DoctorCheck] = []
    for module, package in dependencies.items():
        present = importlib.util.find_spec(module) is not None
        checks.append(
            DoctorCheck(
                code=f"dependency_{module}",
                status="ok" if present else "error",
                message=f"{package} is {'available' if present else 'missing'}",
            )
        )
    return checks


def check_environment(
    targets: Iterable[DoctorTarget],
    command_runner: CommandRunner = _run_command,
    *,
    environ: Mapping[str, str] | None = None,
    endpoint_probe: EndpointProbe = _probe_endpoint,
) -> DoctorReport:
    """Inspect dependencies, Docker metadata, credentials, and endpoints.

    The check is read-only: it never pulls an image, creates a container, or
    mutates a configured database.
    """
    environment = os.environ if environ is None else environ
    selected = tuple(targets)
    checks = _dependency_checks()

    docker_ok, docker_detail = _command_available(
        ["docker", "version", "--format", "{{.Server.Version}}"],
        command_runner,
    )
    checks.append(
        DoctorCheck(
            code="docker_available" if docker_ok else "docker_unavailable",
            status="ok" if docker_ok else "error",
            message=(
                f"Docker server is available ({docker_detail})"
                if docker_ok
                else f"Docker server is unavailable: {docker_detail}"
            ),
        )
    )

    compose_ok, compose_detail = _command_available(
        ["docker", "compose", "version", "--short"],
        command_runner,
    )
    checks.append(
        DoctorCheck(
            code="compose_available" if compose_ok else "compose_unavailable",
            status="ok" if compose_ok else "error",
            message=(
                f"Docker Compose is available ({compose_detail})"
                if compose_ok
                else f"Docker Compose is unavailable: {compose_detail}"
            ),
        )
    )

    target_results: list[DoctorTargetResult] = []
    for target in selected:
        credential_name = target.runtime.password_env
        credential_present = (
            None if not credential_name else bool(environment.get(credential_name))
        )
        if credential_name:
            checks.append(
                DoctorCheck(
                    code=(
                        "credential_present"
                        if credential_present
                        else "credential_missing"
                    ),
                    status="ok" if credential_present else "error",
                    message=(
                        f"credential variable {credential_name} is set"
                        if credential_present
                        else f"credential variable {credential_name} is not set"
                    ),
                    target_id=target.spec.id,
                )
            )

        image_id = resolve_docker_identity(target.spec.image, command_runner)
        checks.append(
            DoctorCheck(
                code="image_available" if image_id else "image_missing",
                status="ok" if image_id else "error",
                message=(
                    f"image {target.spec.image} resolves to {image_id}"
                    if image_id
                    else f"image {target.spec.image} is not available locally"
                ),
                target_id=target.spec.id,
            )
        )

        try:
            reachable = bool(endpoint_probe(target.runtime))
        except Exception as exc:
            reachable = False
            endpoint_message = f"endpoint probe failed: {type(exc).__name__}: {exc}"
        else:
            endpoint_message = (
                "configured endpoint is reachable"
                if reachable
                else "configured endpoint is not reachable"
            )
        checks.append(
            DoctorCheck(
                code="endpoint_reachable" if reachable else "endpoint_unreachable",
                status="ok" if reachable else "error",
                message=endpoint_message,
                target_id=target.spec.id,
            )
        )
        target_results.append(
            DoctorTargetResult(
                target_id=target.spec.id,
                dbms=target.spec.dbms,
                executor_target=target.spec.executor_target,
                image=target.spec.image,
                resolved_image_id=image_id,
                endpoint_reachable=reachable,
                credential_env=credential_name,
                credential_present=credential_present,
            )
        )

    return DoctorReport(checks=tuple(checks), targets=tuple(target_results))


def pair_doctor_targets(
    target_specs: Mapping[str, TargetSpec],
    runtime_targets: Sequence[DbTarget],
    *,
    dbms: str | None = None,
) -> tuple[DoctorTarget, ...]:
    runtime_by_name = {target.name: target for target in runtime_targets}
    paired: list[DoctorTarget] = []
    for target_id in sorted(target_specs):
        spec = target_specs[target_id]
        if dbms is not None and spec.dbms != dbms:
            continue
        runtime = runtime_by_name.get(spec.executor_target)
        if runtime is None:
            runtime = DbTarget(name=spec.executor_target)
        paired.append(DoctorTarget(spec=spec, runtime=runtime))
    return tuple(paired)
