# External Baseline Artifacts

This note records the external graph-database testing artifacts downloaded for
baseline exploration.

## Download Status

The repositories are stored under `external_tools/`. GDsmith, GDBMeter, and
Gamera were downloaded earlier as GitHub archive snapshots because direct
`git clone` failed in the local environment at that time; their original archive
files are preserved in `external_tools/_archives`. GraphGenie and GRev were
later cloned directly from GitHub with shallow history.

| Tool | Repository | Local path | Default branch | Build entry | Status |
| --- | --- | --- | --- | --- | --- |
| GDsmith | https://github.com/ddaa2000/GDsmith | `external_tools/GDsmith` | `master` | Maven, `pom.xml` | Built |
| GDBMeter | https://github.com/gdbmeter/gdbmeter | `external_tools/gdbmeter` | `main` | Gradle wrapper, `build.gradle.kts` | Built |
| Dinkel | https://github.com/CelineWuest/dinkel | `external_tools/dinkel` | `main` | Go module, `go.mod` | Built |
| Gamera | https://github.com/cuhk-seclab/Gamera | `external_tools/Gamera` | `main` | Maven projects under `CypherChecker/` and `GremlinChecker/` | CypherChecker built; GremlinChecker partial |
| GraphGenie | https://github.com/YuanchengJiang/GraphGenie | `external_tools/GraphGenie` | `main` | Python scripts, `main.py` and `graphgenie.ini` | Downloaded |
| GRev | https://github.com/CUHK-Shenzhen-SE/GRev | `external_tools/GRev` | `main` | Docker Compose plus Python clients | Downloaded |
| DGDB | Not found yet | N/A | N/A | N/A | No official repository identified |

Downloaded Git revisions:

| Tool | Commit |
| --- | --- |
| GraphGenie | `12e55c7a4316e757bff075098be299d837153db6` |
| GRev | `11b216ccd1dfc2f12a1e7d65b3fa2e55d4726244` |

## Immediate Build Notes

- GDsmith requires Java 11 and Maven. Its README describes testing Neo4j,
  RedisGraph, and Memgraph through `config.json` and `java -jar GDsmith.jar`.
- GDBMeter requires JDK 11 and uses its bundled Gradle wrapper. Its README shows
  `gradlew clean shadowJar` followed by `java -jar gdbmeter-*.jar`.
- Dinkel requires Go 1.22 or higher and can be built with `go build`. The
  local build uses a portable Go 1.22.0 toolchain under
  `external_tools/go1.22.0` because Go is not installed system-wide.
- Gamera requires Java 8 or Java 17 plus Maven. It has separate Cypher and
  Gremlin checkers.
- GraphGenie is a Python prototype. Its README reports testing with Python
  3.8.10 on Ubuntu 20.04 and requires `configparser`, `neo4j`, `redisgraph`,
  and `psycopg2`.
- GRev is a Docker-Compose based artifact. It includes clients for Neo4j,
  Memgraph, Redis, TinkerPop, and Nebula, plus Elasticsearch/Kibana/Logstash
  monitoring. It expects prepared queries produced by a bundled GDsmith jar.

## Baseline Priority

For the ICSE comparison, prioritize runnable Cypher baselines in this order:

1. GDBMeter, because it is the query-partitioning baseline most directly tied to
   graph database testing.
2. GDsmith, because it is a generator-centric Cypher testing baseline with
   Memgraph/Neo4j support in the README.
3. Dinkel, because it is a recent state-aware Cypher fuzzer, but may require
   more target-specific setup.
4. Gamera CypherChecker, as a metamorphic-testing comparison if its Cypher
   target setup can be aligned with our local engines.
5. GraphGenie, as the ICSE 2024 graph-query-transformation baseline. It is
   relevant for logic-bug comparison but expects an initialized graph dataset.
6. GRev, as the ICSE 2024 equivalent-query-rewriting baseline. It is heavier to
   run because its artifact is organized around Docker Compose and multiple
   target systems.

DGDB should remain a conceptual related-work baseline unless an official
artifact is located.

## Local Build Results on 2026-05-30

Environment:

- Java: OpenJDK 17.0.17
- Maven: Apache Maven 3.8.2
- Gradle: not globally installed, but GDBMeter's bundled wrapper works
- Go: not installed on the host
- Docker: available, but Docker Hub base-image pulls failed for Dinkel

Built artifacts:

| Tool | Build command | Output | Smoke test |
| --- | --- | --- | --- |
| GDBMeter | `.\gradlew.bat clean shadowJar` in `external_tools/gdbmeter` | `external_tools/gdbmeter/build/libs/gdbmeter-1.0.0-SNAPSHOT-all.jar` | `java -jar ... -h` prints usage |
| GDsmith | `mvn -DskipTests package` plus `mvn dependency:copy-dependencies` in `external_tools/GDsmith` | `external_tools/GDsmith/target/GDsmtih-1.0-SNAPSHOT.jar` and `external_tools/GDsmith/target/dependency/*.jar` | Runs with `java -cp target/classes;target/dependency/* org.example.gdsmith.Main --help` |
| Gamera CypherChecker | `mvn -DskipTests package` in `external_tools/Gamera/CypherChecker` | `external_tools/Gamera/CypherChecker/target/cypherchecker-1.0-SNAPSHOT.jar` plus `target/lib/*.jar` | Not smoke-run because its `Main` currently hardcodes a Neo4j run and may clear local database state |
| Gamera GremlinChecker | `mvn -DskipTests package` in `external_tools/Gamera/GremlinChecker` | `external_tools/Gamera/GremlinChecker/target/GremlinChecker-1.0-SNAPSHOT.jar` | Build emitted a Windows console/Jansi error near the end; treat as partial until rerun on Linux or with console-color disabled |
| Dinkel | `docker build -t graph-bug-baseline-dinkel:local .` in `external_tools/dinkel` | N/A | Blocked while pulling `golang:1.22-alpine3.19` and `alpine:latest` from Docker Hub |

Local source patch applied:

- `external_tools/Gamera/CypherChecker/src/main/java/ch/cypherchecker/memgraph/oracle/MemgraphOracleFactory.java`
  now handles `OracleType.EDGE` by returning `null`, matching the existing
  unsupported-oracle behavior for other Memgraph oracle types and allowing Java
  17's exhaustive switch check to pass.

Useful commands:

```powershell
# GDBMeter help
java -jar external_tools/gdbmeter/build/libs/gdbmeter-1.0.0-SNAPSHOT-all.jar -h

# GDsmith help from its project root
$cp = 'target/classes;' + ((Get-ChildItem -Path 'target/dependency' -Filter '*.jar' | ForEach-Object { $_.FullName }) -join ';')
java -cp $cp org.example.gdsmith.Main --help

# Dinkel help from its project root
external_tools/dinkel/dinkel.exe help
external_tools/dinkel/dinkel.exe help fuzz
```

## Dinkel Local Build on 2026-06-02

Docker was unavailable because Docker Desktop was not running. The local host
also did not have Go installed, so a portable official Go toolchain was
downloaded and extracted instead:

- Go archive: `external_tools/_archives/go1.22.0.windows-amd64.zip`
- Go root: `external_tools/go1.22.0/go`
- Module cache: `external_tools/go-mod-cache`
- Build cache: `external_tools/go-build-cache`

The initial `go build` attempt timed out through `proxy.golang.org` over IPv6.
The successful build used:

```powershell
$root = 'd:\sci\works\get bugs\graph-bug-fuzz-mvp'
$go = Join-Path $root 'external_tools\go1.22.0\go\bin\go.exe'
$env:GOMODCACHE = Join-Path $root 'external_tools\go-mod-cache'
$env:GOCACHE = Join-Path $root 'external_tools\go-build-cache'
$env:GOPROXY = 'https://goproxy.cn,direct'
$env:GOSUMDB = 'sum.golang.google.cn'
& $go build -o dinkel.exe .
```

Output:

- Binary: `external_tools/dinkel/dinkel.exe`
- Size: 28,428,800 bytes
- Smoke tests: `dinkel.exe help` and `dinkel.exe help fuzz` both succeed.

Dinkel reports these fuzzing targets: Neo4j, FalkorDB, Apache AGE, Memgraph,
and RedisGraph. It reports three strategies: no oracle, equivalence
transformation, and predicate partitioning.

## Smoke Runs

- `paper/gdbmeter_smoke_run_2026-05-30.md`: 60-second GDBMeter run with
  embedded Neo4j and the `PARTITION` oracle. The artifact generated 5990 query
  log lines and no error/bug/assertion lines before being stopped by timeout.
- `paper/gdbmeter_partition_pilot_2026-05-30.md`: first longer GDBMeter
  `PARTITION` pilot against embedded Neo4j. It planned a 10-minute budget but
  terminated after 231.11 seconds with a generated-query arithmetic assertion,
  so it is setup evidence rather than a fair baseline result.
- `paper/gdbmeter_partition_restart_pilot_2026-05-30.md`: 10-minute
  restart-on-crash GDBMeter pilot. The wrapper ran for 600.186 seconds, observed
  111,054 query-related log matches, and restarted after two early exits with
  generated-query arithmetic assertions.
- `paper/baseline_new_version_compatibility_2026-05-30.md`: compatibility
  smoke for newer local DBMS versions. GDsmith connects to Neo4j 2026.04.0 and
  Memgraph 3.10.1; GDBMeter does not directly support external Neo4j and an
  embedded Neo4j 2026.04.0 probe requires JDK 21.
- `paper/gdsmith_neo4j_2026_04_pilot_2026-05-30.md`: 10-minute GDsmith
  `RANDOM_CRASH` pilot against a dedicated Neo4j 2026.04.0 container. The run
  completed the wall-clock budget without tool crashes and produced 20,426
  query-related log matches; the observed exception signature was invalid
  variable-length relationship property access, not a confirmed DBMS bug.
- `paper/dinkel_neo4j_container_2026-06-02.md`: dedicated Neo4j 2026.04.0
  container for Dinkel on `bolt://localhost:7706`. The working container uses
  disabled auth and APOC through `configs/neo4j_dinkel_container.env`. A
  5-query Dinkel smoke run succeeded.
- `paper/dinkel_neo4j_2026_04_pilot_2026-06-02.md`: 10-minute Dinkel
  `PREDICATE_PARTITIONING` pilot against the dedicated Neo4j 2026.04.0
  container. The run generated 645 Dinkel bugreports, but 631 were
  `RepeatedPropertyInCompositeSchema`, indicating invalid generated schema
  operations rather than confirmed DBMS bugs.
- `paper/coverage_smoke_2026-05-30.md`: technical smoke test showing that
  Neo4j 2026.04.0 Docker containers can be instrumented with JaCoCo and reported
  through `scripts/start_neo4j_jacoco_container.ps1`,
  `scripts/collect_neo4j_jacoco_report.ps1`, and
  `scripts/summarize_jacoco_csv.py`.
