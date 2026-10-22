**Suggested title**
`Write-only CALL subquery may return one empty row`

**ArcadeDB version**
Observed on Docker images:

- `arcadedata/arcadedb:latest`
- `arcadedata/arcadedb:26.3.2`
- `arcadedata/arcadedb:26.4.1-SNAPSHOT`
- `arcadedata/arcadedb:26.4.2`

The same result was also rechecked on a fresh `26.4.2` container.

**Discovered on**
- `2026-05-07`

**Environment**
- Host OS: Windows
- Architecture: x86_64
- Deployment: Docker
- ArcadeDB endpoint: HTTP `/api/v1/command/arcade`
- Request mode matches ArcadeDB Studio:
  - `language: opencypher`
  - `serializer: studio`
- Differential comparison target: Neo4j Docker `neo4j:latest`

**Describe the bug**
ArcadeDB may return one empty row for a `CALL` subquery that performs writes but returns no columns.

In Neo4j, a write-only subquery with no `RETURN` does not produce a result row.
ArcadeDB returns one row whose payload is effectively an empty map.

This is not just a row-shape formatting detail.
It changes row cardinality:

- Neo4j: `0 rows`
- ArcadeDB: `1 row`

**To Reproduce**

Query:

```cypher
CALL {
  CREATE (:N)
}
```

**Expected behavior**
Observed Neo4j result:

```text
0 rows
```

ArcadeDB should also return no rows.

**Actual behavior**
Observed ArcadeDB result on all tested versions:

```text
1 row: {}
```

So the write-only subquery produces a spurious empty row.

**Control cases**

A plain top-level write without `CALL` behaves correctly:

```cypher
CREATE (:N)
```

Observed result on Neo4j and all tested ArcadeDB versions:

```text
0 rows
```

If the subquery explicitly returns a value, both engines behave correctly:

```cypher
CALL {
  CREATE (a)
  RETURN 1 AS x
}
RETURN x;
```

Observed result on Neo4j and all tested ArcadeDB versions:

```text
1
```

So the issue is not that `CALL` itself is broken, and it is not that writes inside `CALL` are ignored.
The failure starts specifically when the `CALL` body performs writes but returns no columns.

The same bug also appears with a slightly richer write-only body:

```cypher
CALL {
  CREATE (a)
  WITH a
  SKIP 0
  CREATE (a)-[:KNOWS]->(b)
}
```

Observed results:

- Neo4j: `0 rows`
- ArcadeDB `latest / 26.3.2 / 26.4.1-SNAPSHOT / 26.4.2`: `1 row: {}`

So this is not tied to the smallest `CREATE`-only body.

**Original differential seed**

This family first resurfaced in the current feature-only random differential in an `IN TRANSACTIONS` shape:

```cypher
CALL {
  CREATE (a)
  WITH a
  SKIP 0
  CREATE (a)-[:KNOWS]->(b)
} IN TRANSACTIONS
```

Observed results:

- Neo4j: `0 rows`
- ArcadeDB `26.4.2`: `1 row: {}`

Direct minimization shows that `IN TRANSACTIONS` is not required.
The smaller root cause is already visible with an ordinary write-only `CALL` subquery.

**Verification environment**
Verified directly against:

- Neo4j Docker container: `neo4j:latest`
- ArcadeDB Docker container: `arcadedata/arcadedb:latest`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.3.2`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.4.1-SNAPSHOT`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.4.2`
- Fresh ArcadeDB Docker container: `arcadedata/arcadedb:26.4.2`
- Local differential-testing harness
- Direct ArcadeDB HTTP execution using the same request mode as Studio

**Stage3 provenance**
- Stage3 run: `historical_bug_queries_parallel_llm_v1_feature_only_random_20260506`
- Stage3 artifact: `data/batch_runs/historical_bug_queries_parallel_llm_v1_feature_only_random_20260506/stage3x10/stage3_main/instantiated_cases.jsonl`
- Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_arcadedb_26_4_2_feature_only_random_20260507`
- Differential bucket: `all_passed_different_result`
- Example source query from this run:
  - `RedisGraph__RedisGraph__363__q001` / `case_336259e6925b`
