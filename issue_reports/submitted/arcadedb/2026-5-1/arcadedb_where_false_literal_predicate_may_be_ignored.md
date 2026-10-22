**Suggested title**
`WHERE false literal predicate may be ignored`

**ArcadeDB version**
Observed on Docker images:

- `arcadedata/arcadedb:latest`
- `arcadedata/arcadedb:26.3.2`
- `arcadedata/arcadedb:26.4.1-SNAPSHOT`
- `arcadedata/arcadedb:26.4.2`

**Discovered on**
- `2026-04-24`

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
ArcadeDB may ignore a literal boolean `false` predicate in `WHERE`.

In the minimized repro below, Neo4j correctly returns no rows because the `WHERE false` predicate rejects every matched row.
ArcadeDB returns the matched node anyway.

This is not only a formatting or serialization difference: the row survives a predicate that should be unconditionally false.

**To Reproduce**

Setup:

```cypher
CREATE (:Person {name:'Alice'});
```

Query:

```cypher
MATCH (p:Person)
WHERE false
RETURN p.name AS name;
```

**Expected behavior**
No rows should be returned.

Neo4j result:

```text
0 rows
```

**Actual behavior**
ArcadeDB returns:

```text
Alice
```

**Control cases**

Equivalent false expressions that do not use the literal `false` are filtered correctly:

```cypher
MATCH (p:Person)
WHERE true = false
RETURN p.name AS name;
```

Observed ArcadeDB result:

```text
0 rows
```

```cypher
MATCH (p:Person)
WHERE 1 = 0
RETURN p.name AS name;
```

Observed ArcadeDB result:

```text
0 rows
```

```cypher
MATCH (p:Person)
WHERE NOT true
RETURN p.name AS name;
```

Observed ArcadeDB result:

```text
0 rows
```

However, boolean expressions containing the literal `false` can still be ignored:

```cypher
MATCH (p:Person)
WHERE false AND p.name = 'Alice'
RETURN p.name AS name;
```

Expected:

```text
0 rows
```

Observed ArcadeDB result:

```text
Alice
```

```cypher
MATCH (p:Person)
WHERE p.name = 'Alice' AND false
RETURN p.name AS name;
```

Expected:

```text
0 rows
```

Observed ArcadeDB result:

```text
Alice
```

This suggests the issue is specifically tied to the boolean literal `false` inside `WHERE`, rather than false-valued predicates in general.

**Stronger variant from the current skeleton differential**

The same family also reproduces in a less trivial pipeline where rows survive `WHERE false` after earlier matching and aggregation context:

```cypher
MATCH (a)-[r:KNOWS]-()
WITH a, r, {name: a.name, friends: collect(r)} AS map
WHERE false
RETURN count(r) AS rel_count;
```

Observed results:

- Neo4j: `0`
- ArcadeDB: `2`

So the bug is not limited to the smallest `MATCH ... WHERE false RETURN ...` form.
Even after carrying matched variables and an unused map projection through `WITH`, ArcadeDB still fails to eliminate the rows.

It also reproduces inside an `OPTIONAL MATCH` pipeline on the current `26.4.x` line:

```cypher
MATCH (n:Node)
OPTIONAL MATCH (m:Node)
WHERE false
RETURN (n.x / n.y) AS div,
       5 < (10 / 2) AS cmp,
       NOT(3 < (0 / 1)) AS neg;
```

Setup:

```cypher
CREATE (:Node {x: 10, y: 20}),
       (:Node {x: 30, y: 40});
```

Observed results:

- Neo4j: `2 rows`
- ArcadeDB `26.4.1-SNAPSHOT` / `26.4.2`: `4 rows`

So in this shape ArcadeDB goes beyond merely keeping rows that should have been filtered out.
It also leaks the optional-branch multiplicity, effectively behaving as if the `WHERE false` inside `OPTIONAL MATCH` was ignored.

The same current-version family also shows up in a downstream-match pipeline, where the ignored `OPTIONAL MATCH ... WHERE false` branch multiplies a later deterministic result:

```cypher
MATCH (a:Person {name: 'Alice'})-[*]->(d:Person)
OPTIONAL MATCH ()-[r]->()
WHERE false
WITH a, d, r
MATCH (a)-[:KNOWS]->(b:Person {name: 'Bob'})
RETURN a { .name, friends: [(a)-[:KNOWS]->(friend) | friend.name] } AS result;
```

Observed results:

- Neo4j: `4 rows`
- ArcadeDB `26.4.1-SNAPSHOT` / `26.4.2`: `16 rows`

So on the current line the ignored `false` does not just keep the optional branch alive.
It can also fan out a later stable match by leaking the hidden multiplicity of that branch.

The same current-version family also appears on variable-length relationship matches, including zero-hop paths:

```cypher
MATCH (p:Person)-[r:KNOWS*0..2]-(q:Person)
WHERE false
RETURN p.name AS p, q.name AS q, size(r) AS pathLength;
```

Setup:

```cypher
CREATE (a:Person {name:'Alice', age:30}),
       (b:Person {name:'Bob', age:25}),
       (c:Person {name:'Charlie', age:35}),
       (a)-[:KNOWS]->(b),
       (b)-[:KNOWS]->(c);
```

Observed results:

- Neo4j: `0 rows`
- ArcadeDB `26.4.2`: `9 rows`

And even the zero-hop-only shape still leaks rows:

```cypher
MATCH (p:Person)-[r:KNOWS*0..0]-(q:Person)
WHERE false
RETURN p.name AS p, q.name AS q, size(r) AS pathLength;
```

Observed results:

- Neo4j: `0 rows`
- ArcadeDB `26.4.2`: `3 rows`

So this is not limited to simple node matches or optional branches.
On the current line, ArcadeDB can also ignore `WHERE false` on variable-length path matches, including zero-length expansions.

The same current-version family also appears after a fixed-length match is filtered by `WHERE false`, carried through `DISTINCT`, and followed by a later stable rematch:

```cypher
MATCH (p:Person)-[:KNOWS]->(friend:Person)
WHERE false
WITH DISTINCT p, friend
MATCH (p)-[:KNOWS]->(other:Person)
RETURN count(*) AS total;
```

Setup:

```cypher
CREATE (a:Person {name: 'Alice'})-[:KNOWS]->(b:Person {name: 'Bob'}),
       (c:Person {name: 'Charlie'})-[:KNOWS]->(d:Person {name: 'Diana'});
```

Observed results:

- Neo4j: `0`
- ArcadeDB `26.4.2`: `2`

So the bug is not limited to leaking rows directly out of the filtered clause.
Even after `WITH DISTINCT`, ArcadeDB can still let the impossible rows survive and drive a later deterministic match.

The same current-version family also appears when an `OPTIONAL MATCH ... WHERE false` branch is explicitly checked for nullness and then used to drive a later write:

```cypher
UNWIND [null, 1, 2] AS x
OPTIONAL MATCH (n:Node)
WHERE false
WITH n, x
WHERE n IS NOT NULL
CREATE (m:NewNode {val: x})-[:REL]->(n)
RETURN m
LIMIT 5;
```

Observed results:

- Neo4j: `0 rows`
- ArcadeDB `26.4.2`: `5 rows`

Example ArcadeDB rows include newly created `NewNode` vertices such as:

```text
(:NewNode)
(:NewNode {val: 1})
(:NewNode {val: 2})
```

So this is not merely a projection leak.
The impossible rows surviving `WHERE false` can directly cause later writes that should never have happened.

The same current-version family also appears when a top-level `MATCH ... WHERE false` is followed by an `OPTIONAL MATCH` and then a `MERGE` write:

```cypher
MATCH (a:Person)
WHERE false
OPTIONAL MATCH (a)-[r:KNOWS]->(b:Person)
MERGE (a)-[:FRIEND_OF {active: true}]->(b)
RETURN a.name;
```

Observed results:

- Neo4j: `0 rows`
- ArcadeDB `26.4.2`: `2 rows`

Observed ArcadeDB output:

```text
Alice
Bob
```

So the impossible rows do not merely survive projection.
They can also flow through an optional branch and trigger a later `MERGE` that should never run.

**Differential seed**
This family was first noticed in the latest Neo4j-vs-ArcadeDB differential run from:

- `ArcadeData__arcadedb__3218__q001`

The original generated query contained:

```cypher
MATCH (p:Person)
WHERE false
WITH p
MATCH (c:Company)
CREATE (p)-[:WORKS_FOR]->(c)
WITH p, c, size([x IN [] | x]) AS emptySize
RETURN p.name, c.name, emptySize, count(*) AS totalCount;
```

Neo4j returned no rows, while ArcadeDB returned a row and performed the downstream match/write path.

**Verification environment**
Verified directly against:

- Neo4j Docker container: `neo4j:latest`
- ArcadeDB Docker container: `arcadedata/arcadedb:latest`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.3.2`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.4.1-SNAPSHOT`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.4.2`
- Local differential-testing harness
- Direct ArcadeDB HTTP execution using the same request mode as Studio

**Stage3 provenance**
This report predates explicit file-level Stage3 provenance tracking.
The underlying differential-seed source query was instantiated in:

- Stage3 run: `historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b`
- Query ID: `ArcadeData__arcadedb__3218__q001`
- Case ID: `case_4f45cb1fa7c9`
- Stage3 artifact: `data/batch_runs/historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b/stage3_main/instantiated_cases.jsonl`

The same family was independently resurfaced and minimized again in the current skeleton differential:

- Stage3 run: `historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b`
- Query ID: `RedisGraph__RedisGraph__2091__q001`
- Case ID: `case_b624af4db930`
- Stage3 artifact: `data/batch_runs/historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b/stage3_main/instantiated_cases.jsonl`
- Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_arcadedb_skeleton_20260430`
- Differential bucket: `all_passed_different_result`

Another current-version supporting seed from the fresh `26.4.2` feature-only random differential:

- Stage3 run: `historical_bug_queries_parallel_llm_v1_feature_only_random_20260506`
- Query ID: `RedisGraph__RedisGraph__3030__q001`
- Case ID: `case_4ed01dd792a1`
- Stage3 artifact: `data/batch_runs/historical_bug_queries_parallel_llm_v1_feature_only_random_20260506/stage3x10/stage3_main/instantiated_cases.jsonl`
- Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_arcadedb_26_4_2_feature_only_random_20260507`
- Differential bucket: `all_passed_different_result`

Another fresh `26.4.2` supporting seed from the same differential:

- Stage3 run: `historical_bug_queries_parallel_llm_v1_feature_only_random_20260506`
- Query ID: `RedisGraph__RedisGraph__1161__q002`
- Case ID: `case_b8d0277ba48f`
- Stage3 artifact: `data/batch_runs/historical_bug_queries_parallel_llm_v1_feature_only_random_20260506/stage3x10/stage3_main/instantiated_cases.jsonl`
- Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_arcadedb_26_4_2_feature_only_random_20260507`
- Differential bucket: `all_passed_different_result`

The current-version `OPTIONAL MATCH ... WHERE false` row-multiplication variant was independently resurfaced in the same skeleton differential:

- Stage3 run: `historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b`
- Query ID: `neo4j__12883__q001`
- Case ID: `case_accee0d2e5df`
- Stage3 artifact: `data/batch_runs/historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b/stage3_main/instantiated_cases.jsonl`
- Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_arcadedb_skeleton_20260430`
- Differential bucket: `all_passed_different_result`

Another current-version `OPTIONAL MATCH ... WHERE false` multiplicity-leak variant was independently resurfaced in the same skeleton differential:

- Stage3 run: `historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b`
- Query ID: `RedisGraph__RedisGraph__197__q002`
- Case ID: `case_314c335bf679`
- Stage3 artifact: `data/batch_runs/historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b/stage3_main/instantiated_cases.jsonl`
- Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_arcadedb_skeleton_20260430`
- Differential bucket: `all_passed_different_result`

The same current-version family was independently resurfaced again in the feature-only random differential on a variable-length path shape:

- Stage3 run: `historical_bug_queries_parallel_llm_v1_feature_only_random_20260506`
- Query ID: `RedisGraph__RedisGraph__1375__q002`
- Case ID: `case_4fccd7f10d96`
- Stage3 artifact: `data/batch_runs/historical_bug_queries_parallel_llm_v1_feature_only_random_20260506/stage3x10/stage3_main/instantiated_cases.jsonl`
- Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_arcadedb_26_4_2_feature_only_random_20260507`
- Differential bucket: `all_passed_different_result`

Another current-version fixed-length rematch variant was independently resurfaced in the same feature-only random differential:

- Stage3 run: `historical_bug_queries_parallel_llm_v1_feature_only_random_20260506`
- Query ID: `ArcadeData__arcadedb__3772__q002`
- Case ID: `case_bdb035529ecc`
- Stage3 artifact: `data/batch_runs/historical_bug_queries_parallel_llm_v1_feature_only_random_20260506/stage3x10/stage3_main/instantiated_cases.jsonl`
- Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_arcadedb_26_4_2_feature_only_random_20260507`
- Differential bucket: `all_passed_different_result`

Another current-version downstream-filter variant was independently resurfaced in the same feature-only random differential:

- Stage3 run: `historical_bug_queries_parallel_llm_v1_feature_only_random_20260506`
- Query ID: `FalkorDB__FalkorDB__762__q002`
- Case ID: `case_2061d2085e03`
- Stage3 artifact: `data/batch_runs/historical_bug_queries_parallel_llm_v1_feature_only_random_20260506/stage3x10/stage3_main/instantiated_cases.jsonl`
- Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_arcadedb_26_4_2_feature_only_random_20260507`
- Differential bucket: `all_passed_different_result`
