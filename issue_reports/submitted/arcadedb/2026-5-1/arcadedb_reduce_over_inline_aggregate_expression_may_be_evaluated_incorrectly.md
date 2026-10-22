done
**Suggested title**
`reduce(...) over an inline aggregate expression may be evaluated incorrectly`

**ArcadeDB version**
Observed on Docker images:

- `arcadedata/arcadedb:26.4.1-SNAPSHOT`
- `arcadedata/arcadedb:26.4.2`

**Discovered on**
- `2026-04-24`

**Environment**
- Host OS: Windows
- Architecture: x86_64
- Deployment: Docker
- ArcadeDB endpoint: HTTP `/api/v1/command/arcade`
- Request mode used for direct reproduction:
  - `language: cypher`
  - `serializer: studio`
- Differential comparison target: Neo4j Docker `neo4j:latest`

**Describe the bug**
ArcadeDB may evaluate `reduce(...)` incorrectly when the list operand is produced by an aggregate expression inline in the same projection.

The problem is not `reduce(...)` by itself, and it is not the aggregate by itself. The boundary is much narrower:

- `WITH ... AS ages RETURN reduce(... IN ages ...)` works
- `RETURN reduce(... IN collect(...) ...)` does not
- `WITH ... AS age_sum RETURN reduce(... IN [age_sum] ...)` works
- `RETURN reduce(... IN [sum(...)] ...)` does not

So the issue appears to be specific to inline aggregate expressions nested directly inside the `reduce(...)` list operand.

**To Reproduce**

Setup:

```cypher
CREATE (:Person {name:'Alice', age:30});
CREATE (:Person {name:'Bob', age:25});
CREATE (:Person {name:'Charlie', age:35});
```

Query:

```cypher
MATCH (p:Person)
RETURN p.name AS name,
       reduce(total = 0, n IN collect(p.age) | total + n) AS total_age_sum
ORDER BY name;
```

**Expected behavior**
Each grouped row should reduce over its own one-element collected list:

```text
Alice,   30
Bob,     25
Charlie, 35
```

Observed Neo4j result:

```text
Alice,   30
Bob,     25
Charlie, 35
```

**Actual behavior**
Observed ArcadeDB `26.4.1-SNAPSHOT` and `26.4.2` result:

```text
Alice,   0
Bob,     0
Charlie, 0
```

So the inline `collect(p.age)` is not being fed into `reduce(...)` correctly.

**Control case**
If the aggregate result is materialized first and only then passed into `reduce(...)`, ArcadeDB behaves normally:

```cypher
MATCH (p:Person)
WITH p.name AS name, collect(p.age) AS ages
RETURN name,
       reduce(total = 0, n IN ages | total + n) AS total_age_sum
ORDER BY name;
```

Observed result on Neo4j and ArcadeDB `26.4.1-SNAPSHOT` / `26.4.2`:

```text
Alice,   30
Bob,     25
Charlie, 35
```

This makes the boundary clear: `reduce(...)` itself works, and `collect(...)` itself works, but the inline aggregate expression inside `reduce(...)` does not.

**Stronger reproducer**
The same family also appears with other inline aggregate expressions, not just `collect(...)`.

For example:

```cypher
MATCH (p:Person)
RETURN p.name AS name,
       reduce(total = 0, n IN [sum(p.age)] | total + n) AS s
ORDER BY name;
```

Observed Neo4j result:

```text
Alice,   30
Bob,     25
Charlie, 35
```

Observed ArcadeDB `26.4.1-SNAPSHOT` / `26.4.2` result:

```text
Alice,   30
Bob,     55
Charlie, 90
```

The same cumulative pattern also appears with `count(*)`:

```cypher
MATCH (p:Person)
RETURN p.name AS name,
       reduce(total = 0, n IN [count(*)] | total + n) AS s
ORDER BY name;
```

Observed Neo4j result:

```text
Alice,   1
Bob,     1
Charlie, 1
```

Observed ArcadeDB `26.4.1-SNAPSHOT` / `26.4.2` result:

```text
Alice,   1
Bob,     2
Charlie, 3
```

So this is not limited to `collect(...)`. More generally, inline aggregate expressions inside `reduce(...)` may be evaluated against the wrong state.

**Additional failure mode**
Without a grouping key, ArcadeDB can also silently lose the projected result column:

```cypher
MATCH (p:Person)
RETURN reduce(total = 0, n IN collect(p.age) | total + n) AS total_age_sum;
```

Observed Neo4j result:

```text
90
```

Observed ArcadeDB `26.4.1-SNAPSHOT` / `26.4.2` result:

```text
<row present, but `total_age_sum` missing>
```

This suggests the same underlying issue can show up either as a wrong numeric result or as a dropped projection value, depending on the aggregation shape.

**Differential seed**
This family was first noticed while compacting:

- `FalkorDB__FalkorDB__460__q003`

The original generated query mixed `MERGE`, `count(*)`, and:

```cypher
reduce(total = 0, n IN collect(p.age) | total + n)
```

After minimization, the write clause turned out not to be required. The core bug is the inline aggregate expression inside `reduce(...)`.

**Verification environment**
Verified directly against:

- Neo4j Docker container: `neo4j:latest`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.4.1-SNAPSHOT`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.4.2`
- Local differential-testing harness
- Direct ArcadeDB HTTP execution

**Stage3 provenance**
This report predates explicit file-level Stage3 provenance tracking.
The underlying differential-seed source query was instantiated in:

- Stage3 run: `historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b`
- Query ID: `FalkorDB__FalkorDB__460__q003`
- Case ID: `case_e2ca1392cc79`
- Stage3 artifact: `data/batch_runs/historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b/stage3_main/instantiated_cases.jsonl`
