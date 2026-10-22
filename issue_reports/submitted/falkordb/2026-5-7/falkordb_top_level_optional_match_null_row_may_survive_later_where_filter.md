**Suggested title**
`Top-level OPTIONAL MATCH null row may survive a later WHERE filter`

**FalkorDB version**
Observed on:

- FalkorDB `4.18.4`

Docker images used locally:
- `falkordb/falkordb:v4.18.3`
- `falkordb/falkordb:v4.18.4`

**Discovered on**
- `2026-05-07`

**Environment**
- Host OS: Windows
- Architecture: x86_64
- Deployment: Docker
- Connection method: RESP / `GRAPH.QUERY`
- Client used for direct reproduction: project FalkorDB executor and direct local reproduction
- Differential comparison target: Neo4j Docker target from the local harness

**Describe the bug**
FalkorDB may keep the null-preserving row from a top-level `OPTIONAL MATCH` even after a later `WHERE` clause should filter that row away.

In Cypher, a top-level `OPTIONAL MATCH` with no match can produce one row where the matched variables are `null`. That part is expected.

The problem is that, on FalkorDB `4.18.3`, a later `WHERE` filter such as `p IS NOT NULL` or `p.age > 0` does not remove that null row. Neo4j correctly returns `0 rows`.

**To Reproduce**
1. Start the FalkorDB Docker container.
2. Run:

```cypher
CREATE (a:Person {name:'Alice', age:30}),
       (b:Person {name:'Bob', age:25}),
       (c:Person {name:'Charlie', age:35});

OPTIONAL MATCH (p:Person)-[:KNOWS]-(q:Person)
WITH p
WHERE p IS NOT NULL
RETURN p.name;
```

**Expected behavior**
Neo4j returns:

```text
0 rows
```

because the top-level `OPTIONAL MATCH` found no `[:KNOWS]` relationships, so the only surviving row has `p = null`, and `WHERE p IS NOT NULL` should remove it.

FalkorDB should return the same result.

**Actual behavior**
Observed FalkorDB `4.18.3` result:

```text
p.name = null
```

So the null-preserving row survives even though the later `WHERE` clause should filter it out.

**Control query 1**
The top-level `OPTIONAL MATCH` null row itself is not the bug. Without the later `WHERE`, both engines preserve one null row:

```cypher
CREATE (a:Person {name:'Alice', age:30}),
       (b:Person {name:'Bob', age:25}),
       (c:Person {name:'Charlie', age:35});

OPTIONAL MATCH (p:Person)-[:KNOWS]-(q:Person)
RETURN p.name;
```

Observed result on both Neo4j and FalkorDB:

```text
p.name = null
```

So the null-preserving `OPTIONAL MATCH` behavior itself is not the issue.

**Control query 2**
The problem is not specific to `IS NOT NULL`. A value predicate that should also reject the null row still leaks it on FalkorDB:

```cypher
CREATE (a:Person {name:'Alice', age:30}),
       (b:Person {name:'Bob', age:25}),
       (c:Person {name:'Charlie', age:35});

OPTIONAL MATCH (p:Person)-[:KNOWS]-(q:Person)
WITH p
WHERE p.age > 0
RETURN p.name;
```

Observed results:

- Neo4j:

```text
0 rows
```

- FalkorDB `4.18.3`:

```text
p.name = null
```

So the later `WHERE` appears to be ineffective on the top-level optional null row, not just mis-implemented for one specific predicate.

**Control query 3**
If the query is anchored with a preceding mandatory `MATCH`, both engines behave normally:

```cypher
CREATE (a:Person {name:'Alice', age:30}),
       (b:Person {name:'Bob', age:25}),
       (c:Person {name:'Charlie', age:35});

MATCH (p:Person)
OPTIONAL MATCH (p)-[:KNOWS]-(q:Person)
WITH p, collect(q.name) AS friends
WHERE p.age > p.age - 1
RETURN p.name, friends
ORDER BY size(friends) DESC;
```

Observed result on both Neo4j and FalkorDB:

```text
Alice,   []
Bob,     []
Charlie, []
```

So the bug is not a generic `OPTIONAL MATCH` + `WHERE` problem. It is specifically about the top-level optional null row surviving a later filter.

**Original differential shape**
The same family surfaced in the current differential run as:

```cypher
OPTIONAL MATCH (p:Person)-[:KNOWS]-(q:Person)
WITH p, collect(q.name) AS friends
WHERE p.age > p.age - 1
RETURN p.name, friends
ORDER BY size(friends) DESC;
```

Observed results:

- Neo4j:

```text
0 rows
```

- FalkorDB `4.18.3`:

```text
p.name = null, friends = []
```

So the issue is not limited to direct projection of `p`. The same leaked null row also survives through aggregation and ordering.

**Why this looks like a real bug**
The boundary is narrow and consistent:

- top-level `OPTIONAL MATCH` with no match: both engines preserve one null row
- later `WHERE` that should reject that row: Neo4j removes it, FalkorDB keeps it
- anchored `MATCH ... OPTIONAL MATCH ... WHERE ...`: both engines behave normally

So this does not look like a generic `OPTIONAL MATCH` difference. It looks like FalkorDB is failing to apply later `WHERE` filtering correctly to the null-preserving row produced by a top-level optional match.

**Differential seed**
This family surfaced while minimizing:

- `neo4j__12996__q003`

**Verification Environment**
Plain FalkorDB Docker image is enough.

Preferred verification target:
- Docker image
- x86_64
- `falkordb/falkordb`

I can verify a fix using:
- plain FalkorDB Docker image on Windows host
- direct local reproduction
- project differential harness

**Stage3 provenance**
- Stage3 run: `historical_bug_queries_parallel_llm_v1_feature_only_random_20260506`
- Stage3 artifact: `data/batch_runs/historical_bug_queries_parallel_llm_v1_feature_only_random_20260506/stage3x10/stage3_main/instantiated_cases.jsonl`
- Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_falkordb_4_18_3_feature_only_random_20260507`
- Differential bucket: `all_passed_different_result`
- Example source query:
  - `neo4j__12996__q003` / `case_dede539b3be1`
