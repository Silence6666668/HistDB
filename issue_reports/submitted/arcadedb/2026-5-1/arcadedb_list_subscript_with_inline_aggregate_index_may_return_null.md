done
**Suggested title**
`List subscript with an inline aggregate index may return null`

**ArcadeDB version**
Observed on Docker images:

- `arcadedata/arcadedb:latest`
- `arcadedata/arcadedb:26.3.2`
- `arcadedata/arcadedb:26.4.1-SNAPSHOT`
- `arcadedata/arcadedb:26.4.2`

**Discovered on**
- `2026-04-25`

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
ArcadeDB may return `null` when a list subscript uses an inline aggregate expression as the index, even though:

- the same aggregate expression evaluates correctly on its own
- the same list subscript works when the index is first materialized in `WITH`

In the minimized repro below, Neo4j returns the first score (`85`).
ArcadeDB returns `null`.

**To Reproduce**

Setup:

```cypher
CREATE (:Person {name:'Alice', scores:[85,92,78]});
```

Query:

```cypher
MATCH (p:Person {name:'Alice'})
OPTIONAL MATCH (p)-[:HAS_FRIEND]->(f:Person)
WITH p, collect(f) AS friends
RETURN p AS person,
       p.scores[toInteger(avg(size(friends)))] AS selectedScore;
```

**Expected behavior**
With no matching `HAS_FRIEND` relationships:

- `collect(f)` becomes `[]`
- `size(friends)` is `0`
- `avg(size(friends))` is `0`
- `toInteger(...)` is `0`

So the query should return the first score:

```text
85
```

Observed Neo4j result:

```text
(:Person {name: "Alice", scores: [85, 92, 78]}), 85
```

**Actual behavior**
ArcadeDB returns the person correctly, but `selectedScore` becomes `null`:

```text
(:Person {name: "Alice", scores: [85, 92, 78]}), null
```

**Control query 1**
If the same index is computed first and then reused, ArcadeDB behaves correctly:

```cypher
MATCH (p:Person {name:'Alice'})
OPTIONAL MATCH (p)-[:HAS_FRIEND]->(f:Person)
WITH p, collect(f) AS friends
WITH p, toInteger(avg(size(friends))) AS idx
RETURN p AS person,
       p.scores[idx] AS selectedScore,
       idx;
```

Observed result on both Neo4j and ArcadeDB:

```text
(:Person {name: "Alice", scores: [85, 92, 78]}), 85, 0
```

So the aggregate expression itself is not the problem.

**Control query 2**
If the subscript uses a non-aggregate index, ArcadeDB also behaves correctly:

```cypher
MATCH (p:Person {name:'Alice'})
OPTIONAL MATCH (p)-[:HAS_FRIEND]->(f:Person)
WITH p, collect(f) AS friends
RETURN p AS person,
       p.scores[size(friends)] AS selectedScore;
```

Observed result on both Neo4j and ArcadeDB:

```text
(:Person {name: "Alice", scores: [85, 92, 78]}), 85
```

This makes the boundary clearer: the failure is tied to using the aggregate expression inline inside the list subscript.

**Differential seed**
This family was first noticed in:

- `RedisGraph__RedisGraph__2271__q002`

**Verification environment**
Verified directly against:

- Neo4j Docker container: `neo4j:latest`
- ArcadeDB Docker containers:
  - `arcadedata/arcadedb:latest`
  - `arcadedata/arcadedb:26.3.2`
  - `arcadedata/arcadedb:26.4.1-SNAPSHOT`
  - `arcadedata/arcadedb:26.4.2`
- ArcadeDB request mode aligned with Studio (`opencypher` + `serializer: studio`)

**Stage3 provenance**
This report predates explicit file-level Stage3 provenance tracking.
The underlying differential-seed source query was instantiated in:

- Stage3 run: `historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b`
- Query ID: `RedisGraph__RedisGraph__2271__q002`
- Case ID: `case_b60c9a310195`
- Stage3 artifact: `data/batch_runs/historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b/stage3_main/instantiated_cases.jsonl`
