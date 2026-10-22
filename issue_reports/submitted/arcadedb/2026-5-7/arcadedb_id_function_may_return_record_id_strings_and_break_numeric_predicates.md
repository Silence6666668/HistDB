done
**Suggested title**
`id()` may return record-id strings and break numeric predicates`

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
ArcadeDB appears to expose `id(n)` as a record-id string such as `#1:0` rather than a numeric id.

As a result, numeric predicates over `id(n)` can silently filter out every row.

In the minimized repro below, Neo4j treats `id(n) >= 0` as true for every matched node.
ArcadeDB accepts the same query but returns `0 rows`.

So this is not just a display difference in how ids are rendered.
The string-like id value also changes query semantics.

**To Reproduce**

Setup:

```cypher
CREATE (:Person {name:'Alice'}),
       (:Person {name:'Bob'});
```

Query:

```cypher
MATCH (n:Person)
WHERE id(n) >= 0
RETURN n.name AS name
ORDER BY name;
```

**Expected behavior**
Observed Neo4j result:

```text
Alice
Bob
```

ArcadeDB should also return both rows.

**Actual behavior**
Observed ArcadeDB result on all tested versions:

```text
0 rows
```

So the numeric predicate over `id(n)` behaves as if no node satisfies it.

**Control cases**

Projecting `id(n)` directly shows that ArcadeDB is returning a string-like record identifier rather than a numeric id:

```cypher
MATCH (n:Person)
RETURN n.name AS name, id(n) AS ident
ORDER BY name;
```

Observed results:

- Neo4j:

```text
Alice, 307
Bob, 308
```

- ArcadeDB examples:

```text
Alice, #1:0
Bob, #1:1
```

The exact record-id prefix can vary across containers, but the value is string-like rather than numeric.

A null-check over `id(n)` still works on both engines:

```cypher
MATCH (n:Person)
WHERE id(n) IS NOT NULL
RETURN n.name AS name
ORDER BY name;
```

Observed result on Neo4j and all tested ArcadeDB versions:

```text
Alice
Bob
```

So the rows themselves are fine.
The bad behavior starts specifically when `id(n)` is used in a numeric predicate such as:

```cypher
id(n) >= 0
```

The same family also affects relationship ids, not just node ids.

For example:

```cypher
MATCH ()-[r:KNOWS]->()
RETURN id(r) AS ident
ORDER BY ident;
```

Observed results:

- Neo4j:

```text
<numeric id>
<numeric id>
```

- ArcadeDB `26.4.2` fresh container:

```text
#12:0
#12:1
```

And a numeric predicate over the same relationship ids breaks in the same way:

```cypher
MATCH ()-[r:KNOWS]->()
WHERE id(r) >= 0
RETURN count(*) AS cnt;
```

Observed results:

- Neo4j: `2`
- ArcadeDB `26.4.2` fresh container: `0`

So this is a broader `id()` semantic mismatch rather than a node-only issue.

**Stronger differential variant**

The same family surfaced in the current feature-only random differential in a slightly noisier form:

```cypher
MATCH (n:Person)
WHERE n.name > 'Bob' AND id(n) >= 0
RETURN n.name, id(n)
ORDER BY n.name;
```

Setup:

```cypher
CREATE (a:Person {name: 'Alice', age: 30}),
       (b:Person {name: 'Bob', age: 25}),
       (c:Person {name: 'Charlie', age: 35}),
       (d:Person {name: 'David', age: 40});
```

Observed results:

- Neo4j:

```text
Charlie, <numeric id>
David,   <numeric id>
```

- ArcadeDB `26.4.2`:

```text
0 rows
```

Direct minimization shows that the string comparison is not required.
The smaller root cause is already visible with:

```cypher
WHERE id(n) >= 0
```

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
  - `ArcadeData__arcadedb__3216__q005` / `case_30e30bc536a5`
- Additional current-version supporting seed:
  - `FalkorDB__FalkorDB__159__q004` / `case_55126782aaa9`
