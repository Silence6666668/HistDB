# Baseline Expansion Matrix

This note fixes the experimental scope for baseline expansion.

Fixed DBMS versions are recorded in `paper/fixed_baseline_versions.md` and
`configs/baseline_target_versions.env.example`. Server-side baseline runs should
source `configs/server_ubuntu_layout.env` before launch so runner defaults use
the pinned images.

## Coverage Policy

Code coverage is measured for Neo4j and Memgraph, but with different
instrumentation stacks. Neo4j uses the dedicated JaCoCo runners. Memgraph uses a
source-instrumented C/C++ build and is reported as Memgraph source line coverage
with `lcov`/`gcov` or `llvm-cov`.

Coverage numbers are compared only within the same DBMS. For example, Full
method vs. Dinkel on Neo4j is a valid coverage comparison, and Full method vs.
Dinkel on Memgraph is a valid coverage comparison. Absolute covered-line counts
between Neo4j and Memgraph are not directly comparable because they use different
codebases, languages, build systems, and coverage tools.

## Coverage Experiments

| Target | Tool / Method | Coverage Metric | Runner |
| --- | --- | --- | --- |
| Neo4j | Full method | Whole JVM / source line coverage | `scripts/run_full_method_online_source_jacoco_48h.ps1` |
| Neo4j | Dinkel | Whole JVM / source line coverage | `scripts/run_dinkel_neo4j_source_jacoco_48h.ps1` |
| Neo4j | GDsmith | Whole JVM / source line coverage | `scripts/run_gdsmith_neo4j_source_jacoco_restart_48h.ps1` |
| Neo4j | GDBMeter | Whole JVM / source line coverage | `scripts/run_gdbmeter_embedded_jacoco_restart_48h.ps1` |
| Memgraph | Full method | Source line coverage | `scripts/run_memgraph_source_lcov_command.sh` |
| Memgraph | Dinkel | Source line coverage | `scripts/run_memgraph_source_lcov_command.sh` |
| Memgraph | GDsmith | Source line coverage | `scripts/run_memgraph_source_lcov_command.sh` |
| Memgraph | GraspDB | Source line coverage | Optional through `scripts/run_memgraph_source_lcov_command.sh` |

Several Neo4j runner filenames still contain `48h` for historical compatibility,
but their current default budget is 24 hours (`86400` seconds).

## Memgraph Coverage Implementation

Memgraph coverage should be collected from a fixed source checkout and a fixed
instrumented build. The recommended setup is:

- build Memgraph from source with GCC coverage flags
  (`CFLAGS=--coverage -O0 -g`, `CXXFLAGS=--coverage -O0 -g`,
  `LDFLAGS=--coverage`) or the equivalent LLVM coverage flags;
- run each method against the same instrumented binary, port, clean database
  policy, timeout policy, and 24-hour budget;
- collect coverage by stopping Memgraph at fixed intervals, capturing
  `lcov`/`gcov` data, restarting Memgraph, and continuing the method runner;
- report cumulative source line coverage over time and final source line
  coverage at 24 hours.

For long-running trend plots, use epoch-based sampling. A 5-minute or 10-minute
epoch is acceptable: run the fuzzer for one epoch, gracefully stop Memgraph so
coverage counters are flushed, collect coverage, restart Memgraph, and continue.
Concrete command recipes are recorded in `paper/memgraph_source_coverage_recipes.md`.

## Non-Coverage Baseline Expansion

These runs collect generated queries, crashes, tool exceptions, restart counts,
and candidate bug reports. They do not collect coverage.

| Target | Tool | Provider Used | Notes |
| --- | --- | --- | --- |
| Neo4j | Dinkel | `neo4j` | Direct target runner; use the fixed Neo4j image/source version. |
| Neo4j | GDsmith | `neo4j` | Direct target runner; the smoke runner waits for Neo4j readiness before launch. |
| Neo4j | GDBMeter | `neo4j` | This artifact uses its embedded Neo4j path for the Neo4j provider. |
| Memgraph | Dinkel | `memgraph` | Direct target runner with crash/restart support. |
| Memgraph | GDsmith | `memgraph` | Direct target runner through the Neo4j Bolt driver. |
| FalkorDB | GDsmith | `redisgraph` | FalkorDB is exercised through the RedisGraph-compatible provider. |
| FalkorDB | GDBMeter | `redis` | Local GDBMeter hard-codes RedisGraph to `localhost:6379`. |
| FalkorDB | Dinkel | `falkordb` | Direct target runner. |
| Apache AGE | Dinkel | `apache-age` | Direct target runner; server setup must allow passwordless local Postgres/AGE access or equivalent configured access. |
| RedisGraph | GDsmith | `redisgraph` | Optional historical target. |
| RedisGraph | GDBMeter | `redis` | Optional historical target; also requires `localhost:6379`. |

Gamera, GraphGenie, GRev, and GRASpDB are included in the compatibility scope,
but they need extra handling before they are fair 24-hour baselines:

| Tool | Focused DBMS Scope | Current Bundle Status |
| --- | --- | --- |
| Gamera | Neo4j, Memgraph, RedisGraph/FalkorDB through CypherChecker; ArcadeDB through the Gremlin-side artifact path | The local `CypherChecker` jar exists, but `Main.java` overwrites CLI arguments with `mockArgs`, so target-specific runs require a small source patch. |
| GraphGenie | Neo4j and RedisGraph/FalkorDB-style Cypher targets | Python sources compile, but fair runs require a preloaded graph dataset/schema and target-specific `graphgenie.ini`. |
| GRev | Neo4j, Memgraph, RedisGraph/FalkorDB-style targets | Neo4j 24h is complete. FalkorDB fixed-version 24h is complete with zero DBMS-bug candidates. Memgraph had one partial/stalled 24h attempt, and a watchdog-protected replacement run is active as `grev_memgraph_3_9_0_24h_server_20260716_watchdog`; not countable until that rerun finishes and candidates are validated. |
| GRASpDB | Neo4j, RedisGraph/FalkorDB, Memgraph, AgensGraph according to the artifact README | Repository `ljhhuxiaoba/GraspDB-main` is available and cloned at `v1.1.0`; the bundled fat jar starts, and the dedicated two-instance smoke passes on Neo4j, Memgraph, and FalkorDB through the RedisGraph provider. |

## Runner

The non-Neo4j matrix runner is:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_non_neo4j_baseline_matrix.ps1 `
  -Tool both `
  -Targets memgraph,falkordb `
  -DurationSeconds 86400 `
  -RunNamePrefix baseline_nocov_24h_YYYYMMDD
```

For a short compatibility smoke run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_non_neo4j_baseline_matrix.ps1 `
  -Tool both `
  -Targets memgraph,falkordb `
  -Smoke `
  -RunNamePrefix baseline_nocov_smoke_YYYYMMDD
```

The runner writes a manifest under `coverage_runs/<RunNamePrefix>/`, while the
tool-specific logs remain under the corresponding baseline directories in
`external_tools/GDsmith/baseline_runs/` and `external_tools/gdbmeter/baseline_runs/`.

For a broader artifact and target compatibility check across all configured
baselines, use:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_baseline_compat_smoke.ps1 `
  -SmokeSeconds 30 `
  -QueryLimit 5 `
  -OutputDir coverage_runs\baseline_compat_smoke_YYYYMMDD
```

The latest local smoke (`coverage_runs\baseline_compat_smoke_final_20260618`)
confirmed direct query execution for Dinkel on Neo4j/Memgraph/FalkorDB/Apache
AGE, GDsmith on Neo4j/Memgraph/FalkorDB, and GDBMeter on Neo4j. GDBMeter on
FalkorDB reaches query execution but reports assertion/tool exceptions and is
therefore marked as `warning`.
