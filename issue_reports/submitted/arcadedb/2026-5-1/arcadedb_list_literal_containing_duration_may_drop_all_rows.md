done
**Suggested title**
`List literal containing duration(...) may drop all rows`

**ArcadeDB version**
Observed on Docker images:

- `arcadedata/arcadedb:26.4.1-SNAPSHOT`
- `arcadedata/arcadedb:26.4.2`

Older images do not reproduce this exact issue because they reject the `duration(...)` function entirely:

- `arcadedata/arcadedb:latest`
- `arcadedata/arcadedb:26.3.2`

**Discovered on**
- `2026-05-01`

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
On ArcadeDB `26.4.1-SNAPSHOT` and `26.4.2`, returning a list literal that contains a `duration(...)` value may silently drop all rows.

This does not look like a general temporal-value problem:

- `RETURN duration({months: 1000000})` works
- returning scalar rows alongside that duration also works

It also does not look like a general list-literal problem.
The failure starts when the duration value is placed inside a returned list literal.

**To Reproduce**

Query:

```cypher
RETURN [1, duration({months: 1000000})] AS x;
```

**Expected behavior**
Neo4j returns one row containing a two-element list:

```text
[1, P83333Y4M]
```

ArcadeDB should return the same single row.

**Actual behavior**
Observed ArcadeDB `26.4.1-SNAPSHOT` / `26.4.2` result:

```text
0 rows
```

So the query is accepted, but the row disappears entirely.

**Control cases**

Returning the duration value by itself works correctly:

```cypher
RETURN duration({months: 1000000}) AS d;
```

Observed result on Neo4j and ArcadeDB `26.4.1-SNAPSHOT` / `26.4.2`:

```text
P83333Y4M
```

Returning scalar rows together with the same duration also works:

```cypher
UNWIND [1, 2, 3] AS num
RETURN num, duration({months: 1000000}) AS d;
```

Observed result on Neo4j and ArcadeDB `26.4.1-SNAPSHOT` / `26.4.2`:

```text
1, P83333Y4M
2, P83333Y4M
3, P83333Y4M
```

But as soon as the same duration is placed inside a list literal, ArcadeDB drops every row:

```cypher
UNWIND [1, 2, 3] AS num
RETURN num, [1, duration({months: 1000000})] AS x;
```

Observed results:

- Neo4j: `3 rows`
- ArcadeDB `26.4.1-SNAPSHOT` / `26.4.2`: `0 rows`

So the problem is not tied to a single-row query shape.
More generally, list materialization fails when one element is a `duration(...)` value.

**Stronger variant from the current skeleton differential**

The same family also surfaced in a richer query shape from the current skeleton run:

```cypher
MATCH (p:Person)
OPTIONAL MATCH (p)-[:KNOWS]->(friend:Person)
WITH p, collect(friend) AS friends
UNWIND [1, 2, 3] AS num
RETURN p.name AS name,
       [size(friends), duration({months: 1000000})] AS payload;
```

Setup:

```cypher
CREATE (:Person {name: 'Alice'}),
       (:Person {name: 'Bob'}),
       (:Person {name: 'Charlie'});

MATCH (a:Person {name:'Alice'}), (b:Person {name:'Bob'}), (c:Person {name:'Charlie'})
CREATE (a)-[:KNOWS]->(b), (b)-[:KNOWS]->(c);
```

Observed results:

- Neo4j: `9 rows`
- ArcadeDB `26.4.1-SNAPSHOT` / `26.4.2`: `0 rows`

If the same query returns `duration({months: 1000000})` as a standalone projected value instead of embedding it in a list, ArcadeDB again behaves correctly.

This makes the boundary much clearer:

- `duration(...)` alone: correct
- list literal alone: correct
- list literal containing `duration(...)`: rows disappear

**Version boundary**
Older images do not reproduce the same wrong-result behavior because they reject the temporal constructor entirely:

- `latest`: `Unknown function 'duration'`
- `26.3.2`: `Unknown function 'duration'`

So this looks like a `26.4.x` regression introduced after `duration(...)` became accepted.

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
- Stage3 run: `historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b`
- Query ID: `neo4j__13164__q001`
- Case ID: `case_6abe4d41381f`
- Stage3 artifact: `data/batch_runs/historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b/stage3_main/instantiated_cases.jsonl`
- Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_arcadedb_skeleton_20260430`
- Differential bucket: `all_passed_different_result`
