# Fixed Baseline DBMS Versions

All server-side baseline experiments should use the following DBMS versions.

| DBMS | Version | Container / Source Pin | Notes |
| --- | --- | --- | --- |
| Neo4j | 2026.03.1 | `neo4j:2026.03.1`; source checkout under `NEO4J_SOURCE_ROOT` | Use this version for Neo4j coverage and Neo4j baseline runs. Source-level JaCoCo reports must use the matching 2026.03.1 source checkout. |
| Memgraph | 3.9.0 | `memgraph/memgraph:3.9.0`; source checkout/build under `MEMGRAPH_SOURCE_ROOT` and `MEMGRAPH_BUILD_ROOT` | The requested "3.9" is pinned to the explicit local tag `3.9.0`. Use the source build for Memgraph coverage. |
| FalkorDB | 4.16.8 | `falkordb/falkordb:v4.16.8` | Used for bug-finding/generalization baselines. |
| Apache AGE | local existing AGE | `apache/age:latest` | This is the AGE image currently present on the local machine. Record the resolved image digest on the server before long runs. |
| ArcadeDB | 26.4.1 | `arcadedata/arcadedb:26.4.1` | If the exact Docker tag is unavailable, the local machine has `arcadedata/arcadedb:26.4.1-SNAPSHOT` as the closest already-present fallback. |

## Server Setup

Copy and edit the layout file on the server:

```bash
cp configs/server_ubuntu_layout.env.example configs/server_ubuntu_layout.env
```

Load the fixed version variables before launching experiments:

```bash
set -a
source configs/server_ubuntu_layout.env
set +a
```

Pull the fixed images:

```bash
bash scripts/pull_server_baseline_images.sh configs/server_ubuntu_layout.env
```

Alternatively, start one fixed-version target through Docker Compose profiles:

```bash
docker compose --env-file configs/server_ubuntu_layout.env \
  -f configs/docker-compose.fixed-baselines.yml \
  --profile neo4j up -d
```

Record image IDs and digests before running 24-hour experiments:

```bash
docker images \
  neo4j:2026.03.1 \
  memgraph/memgraph:3.9.0 \
  falkordb/falkordb:v4.16.8 \
  apache/age:latest \
  arcadedata/arcadedb:26.4.1
```

For Apache AGE and ArcadeDB fallback runs, record the exact local image ID in
the run manifest because `latest` and `SNAPSHOT` tags are mutable.

## Target Configs

| DBMS | Config |
| --- | --- |
| Memgraph source coverage | `configs/db_targets_memgraph_source_coverage_7787.yaml` |
| FalkorDB 4.16.8 | `configs/db_targets_falkordb_4_16_8.yaml` |
| Apache AGE local | `configs/db_targets_apache_age_local.yaml` |
| ArcadeDB 26.4.1 | `configs/db_targets_arcadedb_26_4_1.yaml` |
