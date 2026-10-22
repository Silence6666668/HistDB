#!/usr/bin/env python3
"""Run GRev-style equivalent rewriting against fixed Memgraph/FalkorDB targets.

This wrapper is intentionally thin: it reuses GRev's pattern rewriters and
Memgraph query generator, but avoids the original compose stack's hard-coded
target versions and query-file assumptions.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import signal
import sys
import time
import traceback
from contextlib import contextmanager
from pathlib import Path
from collections.abc import Sequence as SequenceABC
from typing import Iterable, List, Sequence, Tuple


def add_grev_root() -> Path:
    env_root = os.environ.get("GREV_ROOT")
    if env_root:
        root = Path(env_root).resolve()
    else:
        root = Path.cwd().resolve()
    sys.path.insert(0, str(root))
    return root


GREV_ROOT = add_grev_root()

_ORIGINAL_RANDOM_SAMPLE = random.sample


def compat_random_sample(population: object, k: int, *args: object, **kwargs: object) -> list:
    if not isinstance(population, SequenceABC):
        population = list(population)  # type: ignore[arg-type]
    return _ORIGINAL_RANDOM_SAMPLE(population, k, *args, **kwargs)  # type: ignore[arg-type]


random.sample = compat_random_sample

from cypher.memgraph.query_generator import QueryGenerator  # noqa: E402
from mutator.query_transformer import QueryTransformer as CypherQueryTransformer  # noqa: E402
from mutator.redis.query_transformer import QueryTransformer as RedisQueryTransformer  # noqa: E402
from neo4j import GraphDatabase  # noqa: E402
from neo4j.exceptions import ConfigurationError  # noqa: E402
from redis import Redis as RedisConnection  # noqa: E402
from redis.exceptions import ResponseError  # noqa: E402
from redisgraph import Graph  # noqa: E402


QueryCase = str | Tuple[str, str]


class TopK:
    def __init__(self, size: int) -> None:
        self.size = size
        self.values: List[float] = []

    def offer(self, value: float) -> bool:
        if value <= 1:
            return False
        self.values.append(value)
        self.values = sorted(self.values, reverse=True)[: self.size]
        return value in self.values


class OperationTimeout(TimeoutError):
    pass


@contextmanager
def operation_timeout(seconds: float, phase: str):
    if seconds <= 0 or not hasattr(signal, "SIGALRM"):
        yield
        return

    def timeout_handler(signum, frame):  # noqa: ARG001
        raise OperationTimeout(f"{phase} exceeded {seconds:.1f}s")

    old_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)


def truncate_text(value: object, limit: int = 4000) -> str:
    text = str(value)
    if len(text) > limit:
        return text[:limit] + "...<truncated>"
    return text


class MemgraphAdapter:
    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.driver = self._open_driver()

    def _open_driver(self):
        config = {
            "connection_timeout": 10.0,
            "connection_acquisition_timeout": 10.0,
            "max_transaction_retry_time": 0.0,
            "liveness_check_timeout": 5.0,
        }
        try:
            return GraphDatabase.driver(f"bolt://{self.host}:{self.port}", auth=None, **config)
        except ConfigurationError as exc:
            if "liveness_check_timeout" not in str(exc):
                raise
            config.pop("liveness_check_timeout", None)
            return GraphDatabase.driver(f"bolt://{self.host}:{self.port}", auth=None, **config)

    def reset(self) -> None:
        try:
            self.driver.close()
        except Exception:
            pass
        self.driver = self._open_driver()

    def close(self) -> None:
        self.driver.close()

    def clear(self) -> None:
        with self.driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n").consume()

    def batch_run(self, queries: Sequence[str]) -> None:
        with self.driver.session() as session:
            for query in queries:
                if query.strip():
                    session.run(query).consume()

    def run(self, query: str) -> Tuple[list, float]:
        started = time.time()
        with self.driver.session() as session:
            rows = [record.data() for record in session.run(query)]
        return rows, (time.time() - started) * 100000


class FalkorAdapter:
    def __init__(self, host: str, port: int, graph_name: str, socket_timeout: float) -> None:
        self.redis = RedisConnection(host=host, port=port, socket_timeout=socket_timeout)
        self.graph = Graph(graph_name, self.redis)

    def clear(self) -> None:
        try:
            self.graph.delete()
        except ResponseError:
            pass

    def batch_run(self, queries: Sequence[str]) -> None:
        for query in queries:
            cleaned = clean_query(query)
            if cleaned:
                self.graph.query(cleaned)

    def run(self, query: str) -> Tuple[list, float]:
        cleaned = clean_query(query)
        result = self.graph.query(cleaned)
        return result.result_set, result.run_time_ms


def clean_query(query: str) -> str:
    return query.strip().rstrip(";")


def safe_repr(value: object, limit: int = 2000) -> str:
    text = repr(value)
    if len(text) > limit:
        return text[:limit] + "...<truncated>"
    return text


def canonical_rows(rows: Iterable[object]) -> List[str]:
    return sorted(str(row) for row in rows)


def deep_sort(value: object) -> object:
    if isinstance(value, list):
        return sorted((deep_sort(item) for item in value), key=str)
    return str(value)


def compare_memgraph(left: list, right: list) -> bool:
    if len(left) != len(right):
        return False
    return canonical_rows(left) == canonical_rows(right)


def compare_falkor(left: list, right: list) -> bool:
    if len(left) != len(right):
        return False
    if len(left) >= 9000:
        return True
    left_sorted = [deep_sort(item) for item in left]
    right_sorted = [deep_sort(item) for item in right]
    left_sorted.sort(key=str)
    right_sorted.sort(key=str)
    return left_sorted == right_sorted


def is_test_query(line: str) -> bool:
    upper = f" {line.upper()} "
    return " RETURN " in upper or upper.strip().startswith("RETURN ")


def parse_query_log(path: Path, query_limit: int = 0) -> Tuple[List[str], List[QueryCase]]:
    create_statements: List[str] = []
    query_statements: List[QueryCase] = []
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("--"):
            continue
        if is_test_query(line):
            query_statements.append(line)
        else:
            create_statements.append(line)
    if query_limit > 0:
        query_statements = query_statements[:query_limit]
    return create_statements, query_statements


def generate_memgraph_cases(output_dir: Path, count: int) -> Tuple[List[str], List[QueryCase]]:
    schema_path = output_dir / f"memgraph_schema_{int(time.time() * 1000)}_{random.randint(0, 999999)}.cypher"
    generator = QueryGenerator(str(schema_path))
    create_statements = [
        line.strip()
        for line in schema_path.read_text(encoding="utf-8", errors="replace").splitlines()
        if line.strip()
    ]
    cases = [generator.gen_query() for _ in range(count)]
    return create_statements, cases


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def write_current_operation(args: argparse.Namespace, state: dict, phase: str, **fields: object) -> None:
    payload = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "phase": phase,
        "tool": "GRev",
        "target": args.target,
        "epochs": state.get("epochs"),
        "executed_pairs": state.get("executed_pairs"),
        "exceptions": state.get("exceptions"),
        "logic_inconsistencies": state.get("logic_inconsistencies"),
        "performance_signals": state.get("performance_signals"),
    }
    for key, value in fields.items():
        payload[key] = truncate_text(value)
    write_json(Path(args.output_dir) / "current_operation.json", payload)


def append_jsonl(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def append_logic_tsv(path: Path, target: str, source: str, q1: str, q2: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow([target, source, q1, q2])


def mutate_query(mutator: object, query: str) -> str:
    return mutator.mutant_query_generator(query)


def is_connection_like_exception(exc: BaseException) -> bool:
    exc_name = type(exc).__name__
    exc_text = str(exc).lower()
    return (
        isinstance(exc, OperationTimeout)
        or exc_name
        in {
            "BoltError",
            "ClientError",
            "ConnectionUnavailable",
            "ServiceUnavailable",
            "SessionExpired",
            "TransientError",
        }
        or "connection" in exc_text
        or "pool" in exc_text
    )


def reset_client_after_exception(client: object, exc: BaseException) -> bool:
    if not hasattr(client, "reset") or not is_connection_like_exception(exc):
        return False
    try:
        client.reset()  # type: ignore[attr-defined]
        return True
    except Exception:
        return False


def run_pair(args: argparse.Namespace, state: dict, client: object, q1: str, q2: str) -> None:
    started = time.time()
    try:
        write_current_operation(args, state, "run_pair_q1", query=q1)
        with operation_timeout(args.operation_timeout_seconds, "run_pair_q1"):
            result1 = client.run(q1)
        write_current_operation(args, state, "run_pair_q2", query=q2)
        with operation_timeout(args.operation_timeout_seconds, "run_pair_q2"):
            result2 = client.run(q2)
        rows1, time1 = result1
        rows2, time2 = result2
        state["executed_pairs"] += 1

        if args.target == "memgraph":
            same = compare_memgraph(rows1, rows2)
            metric = max(time1, time2) / (min(time1, time2) + 100)
            report_perf = state["_top_perf"].offer(metric)
        else:
            same = compare_falkor(rows1, rows2)
            big = max(time1, time2)
            small = min(time1, time2)
            metric = (big - small) / big if big > 0 else 0
            report_perf = metric >= 0.8 and (big - small) >= 1000.0

        if not same:
            state["logic_inconsistencies"] += 1
            event = {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "target": args.target,
                "tag": "logic_inconsistency",
                "query1": q1,
                "query2": q2,
                "query_res1": safe_repr(rows1),
                "query_res2": safe_repr(rows2),
                "query_time1": time1,
                "query_time2": time2,
            }
            append_jsonl(Path(args.output_dir) / "events.jsonl", event)
            append_logic_tsv(Path(args.output_dir) / "logic_error.tsv", args.target, state["source"], q1, q2)

        if report_perf:
            state["performance_signals"] += 1
            append_jsonl(
                Path(args.output_dir) / "events.jsonl",
                {
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "target": args.target,
                    "tag": "performance_inconsistency",
                    "metric": metric,
                    "query1": q1,
                    "query2": q2,
                    "query_res1": safe_repr(rows1),
                    "query_res2": safe_repr(rows2),
                    "query_time1": time1,
                    "query_time2": time2,
                },
            )
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, OperationTimeout):
            state["operation_timeouts"] += 1
        if reset_client_after_exception(client, exc):
            state["driver_resets"] += 1
        state["exceptions"] += 1
        append_jsonl(
            Path(args.output_dir) / "events.jsonl",
            {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "target": args.target,
                "tag": "exception",
                "query1": q1,
                "query2": q2,
                "exception_type": type(exc).__name__,
                "exception": str(exc),
                "traceback": traceback.format_exc(limit=10),
                "elapsed_seconds": round(time.time() - started, 6),
            },
        )


def save_summary(args: argparse.Namespace, state: dict, final: bool = False) -> None:
    payload = {
        key: value
        for key, value in state.items()
        if not key.startswith("_")
    }
    payload["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    payload["final"] = final
    write_json(Path(args.output_dir) / "summary.json", payload)


def build_cases(args: argparse.Namespace, state: dict) -> Tuple[List[str], List[QueryCase]]:
    if args.target == "memgraph" and args.generator == "native":
        state["source"] = "grev_native_memgraph_generator"
        return generate_memgraph_cases(Path(args.output_dir) / "generated", args.query_limit)
    if not args.input_file:
        raise ValueError(f"{args.target} needs --input-file when generator is not native")
    state["source"] = str(args.input_file)
    return parse_query_log(Path(args.input_file), args.query_limit)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=["memgraph", "falkordb"], required=True)
    parser.add_argument("--host", default="host.docker.internal")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--duration-seconds", type=int, default=86400)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--input-file", default="")
    parser.add_argument("--generator", choices=["native", "input"], default="input")
    parser.add_argument("--query-limit", type=int, default=2000)
    parser.add_argument("--transform-times", type=int, default=5)
    parser.add_argument("--graph-name", default="grev")
    parser.add_argument("--socket-timeout", type=float, default=10.0)
    parser.add_argument("--operation-timeout-seconds", type=float, default=60.0)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.target == "memgraph":
        client = MemgraphAdapter(args.host, args.port)
        mutator = CypherQueryTransformer()
    else:
        client = FalkorAdapter(args.host, args.port, args.graph_name, args.socket_timeout)
        mutator = RedisQueryTransformer()

    state = {
        "tool": "GRev",
        "target": args.target,
        "mode": "fixed_target_wrapper_equivalent_rewrite",
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_seconds": args.duration_seconds,
        "host": args.host,
        "port": args.port,
        "source": "",
        "epochs": 0,
        "loaded_create_statements": 0,
        "loaded_query_cases": 0,
        "executed_pairs": 0,
        "logic_inconsistencies": 0,
        "performance_signals": 0,
        "exceptions": 0,
        "operation_timeouts": 0,
        "driver_resets": 0,
        "epoch_setup_exceptions": 0,
        "_top_perf": TopK(10),
    }

    deadline = time.monotonic() + args.duration_seconds
    last_summary = 0.0
    try:
        while time.monotonic() < deadline:
            create_statements, cases = build_cases(args, state)
            if not cases:
                raise ValueError("No test queries were loaded or generated.")
            state["epochs"] += 1
            state["loaded_create_statements"] = len(create_statements)
            state["loaded_query_cases"] = len(cases)

            try:
                write_current_operation(args, state, "clear")
                with operation_timeout(args.operation_timeout_seconds, "clear"):
                    client.clear()
                write_current_operation(args, state, "batch_run_create", create_count=len(create_statements))
                with operation_timeout(max(args.operation_timeout_seconds, 300.0), "batch_run_create"):
                    client.batch_run(create_statements)
            except Exception as exc:  # noqa: BLE001
                if isinstance(exc, OperationTimeout):
                    state["operation_timeouts"] += 1
                if reset_client_after_exception(client, exc):
                    state["driver_resets"] += 1
                state["exceptions"] += 1
                state["epoch_setup_exceptions"] += 1
                append_jsonl(
                    output_dir / "events.jsonl",
                    {
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "target": args.target,
                        "tag": "epoch_setup_exception",
                        "epoch": state["epochs"],
                        "exception_type": type(exc).__name__,
                        "exception": str(exc),
                        "traceback": traceback.format_exc(limit=10),
                    },
                )
                save_summary(args, state)
                time.sleep(1)
                continue

            for case_index, case in enumerate(cases):
                if time.monotonic() >= deadline:
                    break
                if isinstance(case, tuple):
                    run_pair(args, state, client, case[0], case[1])
                else:
                    try:
                        write_current_operation(args, state, "run_original", case_index=case_index, query=case)
                        with operation_timeout(args.operation_timeout_seconds, "run_original"):
                            result1 = client.run(case)
                        for transform_index in range(args.transform_times):
                            if time.monotonic() >= deadline:
                                break
                            write_current_operation(
                                args,
                                state,
                                "mutate_query",
                                case_index=case_index,
                                transform_index=transform_index,
                                query=case,
                            )
                            with operation_timeout(args.operation_timeout_seconds, "mutate_query"):
                                mutated = mutate_query(mutator, case)
                            write_current_operation(
                                args,
                                state,
                                "run_mutated",
                                case_index=case_index,
                                transform_index=transform_index,
                                query=mutated,
                            )
                            with operation_timeout(args.operation_timeout_seconds, "run_mutated"):
                                rows2, time2 = client.run(mutated)
                            rows1, time1 = result1
                            state["executed_pairs"] += 1

                            if args.target == "memgraph":
                                same = compare_memgraph(rows1, rows2)
                                metric = max(time1, time2) / (min(time1, time2) + 100)
                                report_perf = state["_top_perf"].offer(metric)
                            else:
                                same = compare_falkor(rows1, rows2)
                                big = max(time1, time2)
                                small = min(time1, time2)
                                metric = (big - small) / big if big > 0 else 0
                                report_perf = metric >= 0.8 and (big - small) >= 1000.0

                            if not same:
                                state["logic_inconsistencies"] += 1
                                append_jsonl(
                                    output_dir / "events.jsonl",
                                    {
                                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                        "target": args.target,
                                        "tag": "logic_inconsistency",
                                        "query1": case,
                                        "query2": mutated,
                                        "query_res1": safe_repr(rows1),
                                        "query_res2": safe_repr(rows2),
                                        "query_time1": time1,
                                        "query_time2": time2,
                                    },
                                )
                                append_logic_tsv(output_dir / "logic_error.tsv", args.target, state["source"], case, mutated)

                            if report_perf:
                                state["performance_signals"] += 1
                                append_jsonl(
                                    output_dir / "events.jsonl",
                                    {
                                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                        "target": args.target,
                                        "tag": "performance_inconsistency",
                                        "metric": metric,
                                        "query1": case,
                                        "query2": mutated,
                                        "query_res1": safe_repr(rows1),
                                        "query_res2": safe_repr(rows2),
                                        "query_time1": time1,
                                        "query_time2": time2,
                                    },
                                )
                    except Exception as exc:  # noqa: BLE001
                        if isinstance(exc, OperationTimeout):
                            state["operation_timeouts"] += 1
                        if reset_client_after_exception(client, exc):
                            state["driver_resets"] += 1
                        state["exceptions"] += 1
                        append_jsonl(
                            output_dir / "events.jsonl",
                            {
                                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                "target": args.target,
                                "tag": "exception",
                                "query1": case,
                                "exception_type": type(exc).__name__,
                                "exception": str(exc),
                                "traceback": traceback.format_exc(limit=10),
                            },
                        )

                now = time.monotonic()
                if now - last_summary >= 30:
                    save_summary(args, state)
                    last_summary = now
        save_summary(args, state, final=True)
        return 0
    except Exception as exc:  # noqa: BLE001
        state["fatal_error"] = f"{type(exc).__name__}: {exc}"
        state["fatal_traceback"] = traceback.format_exc(limit=20)
        save_summary(args, state, final=True)
        append_jsonl(
            output_dir / "events.jsonl",
            {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "target": args.target,
                "tag": "fatal",
                "exception_type": type(exc).__name__,
                "exception": str(exc),
                "traceback": traceback.format_exc(limit=20),
            },
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
