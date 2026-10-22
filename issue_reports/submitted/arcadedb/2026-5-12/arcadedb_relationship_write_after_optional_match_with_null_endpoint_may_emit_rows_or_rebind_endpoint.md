**Suggested title**
`Relationship write after OPTIONAL MATCH with a null endpoint may emit rows or rebind the endpoint`

**ArcadeDB version**
Observed on Docker images:

- `arcadedata/arcadedb:26.4.2`

**Discovered on**
- `2026-05-12`

**Environment**
- Host OS: Windows
- Architecture: x86_64
- Deployment: Docker
- ArcadeDB endpoint: HTTP `/api/v1/command/arcade`
- Request mode matches ArcadeDB Studio:
  - `language: opencypher`
  - `serializer: studio`
- Differential comparison target: Neo4j Community `2026.04.0`

**Describe the bug**
If an `OPTIONAL MATCH` leaves a relationship endpoint variable as `null`, a later relationship write using that variable may still emit rows on ArcadeDB instead of dropping the row.

In the minimized `MERGE` repro below, the optional pattern does not match, so `b` is `null`.
Neo4j returns no rows.
ArcadeDB returns a row and even reports `b.id = 1`, as if the missing endpoint had been rebound to the source node `a`.

This does not look like a general `OPTIONAL MATCH` problem, because the same `OPTIONAL MATCH` without the later write behaves correctly on both engines. It also does not look like a general relationship write problem, because the same `MERGE` works normally once the endpoint is rebound explicitly through a later `MATCH`.

**To Reproduce**

Setup:

```cypher
CREATE (a:Node {id: 1}),
       (b:Node {id: 2});
```

Query:

```cypher
MATCH (a:Node {id: 1})
OPTIONAL MATCH (a)-[:KNOWS]->(b:Node {id: 2})
WITH a, b
MERGE (a)-[:KNOWS]->(b)
RETURN a.id AS aid, b.id AS bid;
```

**Expected behavior**
The `OPTIONAL MATCH` does not bind `b`, so the later `MERGE` pattern has a null endpoint.

Observed Neo4j result:

```text
0 rows
```

ArcadeDB should behave the same way.

**Actual behavior**
Observed ArcadeDB `26.4.2` result:

```text
aid = 1, bid = 1
```

So ArcadeDB keeps the row alive and behaves as if the missing endpoint had become the source node.

**Control cases**

The same `OPTIONAL MATCH` without the later write behaves correctly on both engines:

```cypher
MATCH (a:Node {id: 1})
OPTIONAL MATCH (a)-[:KNOWS]->(b:Node {id: 2})
WITH a, b
RETURN a.id AS aid, b.id AS bid;
```

Observed result on Neo4j and ArcadeDB `26.4.2`:

```text
aid = 1, bid = null
```

So the `OPTIONAL MATCH` itself is fine. The failure starts when the null endpoint is used by a later relationship write.

If the endpoint is rebound explicitly before the write, both engines behave correctly:

```cypher
MATCH (a:Node {id: 1})
OPTIONAL MATCH (a)-[:KNOWS]->(b:Node {id: 2})
WITH a, b
MATCH (c:Node {id: 2})
MERGE (a)-[:KNOWS]->(c)
RETURN a.id AS aid, c.id AS cid, count(*) AS cnt;
```

Observed result on Neo4j and ArcadeDB `26.4.2`:

```text
aid = 1, cid = 2, cnt = 1
```

So this is not a general `MERGE` problem either. The bug is specific to using the null endpoint from the `OPTIONAL MATCH`.

And a direct non-optional `MERGE` also behaves correctly on both engines:

```cypher
MATCH (a:Node {id: 1}), (b:Node {id: 2})
MERGE (a)-[:KNOWS]->(b)
RETURN count(*) AS cnt;
```

Observed result on Neo4j and ArcadeDB `26.4.2`:

```text
cnt = 1
```

**Related CREATE variant**
The same family also appears with `CREATE` instead of `MERGE`:

```cypher
MATCH (a:Node {id: 1})
OPTIONAL MATCH (a)-[:KNOWS]->(b:Node {id: 2})
WITH a, b
CREATE (a)-[:KNOWS]->(b)
RETURN count(*) AS cnt;
```

Observed results:

- Neo4j `2026.04.0`:

```text
0 rows
```

- ArcadeDB `26.4.2`:

```text
cnt = 1
```

So the issue is not limited to `MERGE`. Relationship writes more generally may ignore the fact that the endpoint variable is null.

**Why this is kept separate**
This is intentionally kept separate from the existing ArcadeDB variable-rebinding reports.

- the existing anonymous-`CREATE` rebinding report is about rebinding a scalar `UNWIND` variable to a created node
- this report is about a null relationship endpoint coming from `OPTIONAL MATCH`
- the existing `WITH`-carried-node-null report is about carried node variables becoming `null` after later writes
- this report is about the opposite direction: a `null` endpoint is later treated as if it were usable in a relationship write

So the boundary is different.

**Verification environment**
Verified directly against:

- Neo4j Docker container: `neo4j:2026.04.0`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.4.2`
- local differential-testing harness
- direct fresh-container reruns

**Stage3 provenance**
This family was surfaced in the current feature-only random run as:

- Stage3 run: `historical_bug_queries_parallel_llm_v1_feature_only_random_20260511`
- Query ID: `RedisGraph__RedisGraph__1286__q006`
- Case ID: `case_d1a7b1e04985`
- Stage3 artifact: `data/batch_runs/historical_bug_queries_parallel_llm_v1_feature_only_random_20260511/stage3x10/stage3_main/instantiated_cases.jsonl`
- Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_2026_04_0_arcadedb_26_4_2_feature_only_random_20260512b`
- Differential bucket: `all_passed_different_result`
