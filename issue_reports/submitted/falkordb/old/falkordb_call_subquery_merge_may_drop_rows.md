## FalkorDB version
Observed on Docker image:

- `falkordb/falkordb-server:latest`
- local container tag in use during verification: `4.16.8`
- local container tag in use during revalidation: `4.18.1`
- local container tag in use during current-version revalidation: `v4.18.2`
- local container tag in use during later current-version revalidation: `v4.18.3`

## Discovered on
- `2026-04-23`

## Environment
- Docker on Windows host
- FalkorDB queried through the existing RESP-based executor

## Describe the bug
When a `CALL { ... }` subquery receives multiple outer bindings and performs a per-row `MERGE`, FalkorDB may drop one of the input rows from the final result.

In the minimized repro below, `John` is connected to exactly two neighbors: `Anna` and `Germany`.
The query returns both names on the reference engine, but FalkorDB returns only `Anna`.

## To Reproduce

### Setup
```cypher
CREATE (j:Person {name: 'John'}),
       (a:Person {name: 'Anna'}),
       (g:Country {name: 'Germany'}),
       (j)-[:KNOWS]->(a),
       (j)-[:VISITED]->(g);
```

### Query
```cypher
MATCH (:Person {name: 'John'})--(m)
CALL {
  WITH m
  MERGE (m)-[:LOCATED_IN]->(:City {name: 'Paris'})
  RETURN m.name AS connected_name
}
RETURN connected_name
ORDER BY connected_name
```

## Expected behavior
Both outer bindings should be preserved:

```text
connected_name = Anna
connected_name = Germany
```

## Actual behavior
FalkorDB returns only:

```text
connected_name = Anna
```

The row for `Germany` is missing.

## Control case
Moving the `MERGE` out of the subquery makes the result align:

```cypher
MATCH (:Person {name: 'John'})--(m)
MERGE (m)-[:LOCATED_IN]->(:City {name: 'Paris'})
RETURN m.name AS connected_name
ORDER BY connected_name
```

Observed result:

```text
connected_name = Anna
connected_name = Germany
```

This suggests the issue is tied to row propagation through `CALL { ... }`, not the `MERGE` itself.

## Additional context
- The issue still reproduces without `OPTIONAL MATCH`.
- The issue still reproduces without `LIMIT`.
- A simplified setup with one `John` node and exactly two neighbors is sufficient.
- The issue also still reproduces on FalkorDB `4.18.1`.

## Additional supporting reproducer
The same family also reproduces with scalar outer bindings, not just node bindings:

```cypher
CREATE (:Person {name:'Alice'}),
       (:Person {name:'Bob'});

MATCH (p:Person)
WHERE p.name = 'Alice' OR p.name = 'Bob'
WITH p.name AS personName
CALL {
  WITH personName
  MERGE (n:Person {name: personName})
  RETURN n
}
WITH personName, n
MATCH (m:Person)
WHERE m.name IN [personName]
RETURN personName AS aliasName, COUNT(m) AS count
ORDER BY aliasName;
```

Observed Neo4j result:

```text
Alice, 1
Bob,   1
```

Observed FalkorDB result:

```text
Bob, 1
```

So the dropped-row behavior is not limited to passing node values into the subquery. A scalar alias carried into `CALL { ... }` and used in a per-row `MERGE` can also lose one outer row.

**Control for the supporting reproducer**
If the subquery becomes read-only, both rows survive on both engines:

```cypher
CREATE (:Person {name:'Alice'}),
       (:Person {name:'Bob'});

MATCH (p:Person)
WHERE p.name = 'Alice' OR p.name = 'Bob'
WITH p.name AS personName
CALL {
  WITH personName
  RETURN personName AS echoed
}
RETURN echoed
ORDER BY echoed;
```

Observed result on both Neo4j and FalkorDB:

```text
Alice
Bob
```

This keeps the boundary consistent: the problem is tied to row propagation through `CALL { ... }` when the subquery performs a write, not to `CALL` with scalar aliases in general.

## Additional supporting reproducer 2 (current version)
The same family also reproduces on FalkorDB `v4.18.2` when the subquery is fed by a repeated scalar outer binding and the result is aggregated back into a list:

```cypher
UNWIND [x IN range(1,3) | x] AS num
CALL {
  MERGE (p:Person {name: 'Alice', age: 30})
  RETURN p.name AS name
}
RETURN [y IN collect(name) WHERE y IS NOT NULL | y] AS result;
```

Observed results:

- Neo4j:

```text
['Alice', 'Alice', 'Alice']
```

- FalkorDB `v4.18.2`:

```text
['Alice']
```

So FalkorDB again collapses multiple outer rows down to a single surviving row after a write inside `CALL { ... }`.

**Control for supporting reproducer 2**
If the subquery becomes read-only, both engines keep all three rows:

```cypher
UNWIND [x IN range(1,3) | x] AS num
CALL {
  RETURN 'Alice' AS name
}
RETURN [y IN collect(name) WHERE y IS NOT NULL | y] AS result;
```

Observed result on both Neo4j and FalkorDB:

```text
['Alice', 'Alice', 'Alice']
```

Likewise, if the `MERGE` is moved out of the subquery, both engines also keep all three rows:

```cypher
UNWIND [x IN range(1,3) | x] AS num
MERGE (p:Person {name: 'Alice', age: 30})
RETURN [y IN collect(p.name) WHERE y IS NOT NULL | y] AS result;
```

Observed result on both Neo4j and FalkorDB:

```text
['Alice', 'Alice', 'Alice']
```

This keeps the boundary narrow: the row-loss behavior appears when the write happens inside `CALL { ... }`, not for repeated outer rows in general.

**Additional supporting reproducer 3 (later current version)**
The same family also reproduces on FalkorDB `v4.18.3` with per-row node pairs feeding a write subquery:

```cypher
CREATE (a:Person {name: 'Alice'}),
       (b:Person {name: 'Bob'}),
       (c:Person {name: 'Charlie'});

UNWIND [['Alice', 'Bob'], ['Bob', 'Charlie']] AS pair
MATCH (x:Person {name: pair[0]}), (y:Person {name: pair[1]})
CALL {
  WITH x, y
  MERGE (x)-[:KNOWS {since: 2020}]->(y)
  RETURN 1 AS dummy
}
RETURN x.name AS from, y.name AS to
ORDER BY from, to;
```

Observed results:

- Neo4j:

```text
Alice, Bob
Bob,   Charlie
```

- FalkorDB `v4.18.3`:

```text
Alice, Bob
```

So FalkorDB again drops one of the outer rows after a write inside `CALL { ... }`, even though both input pairs are valid and independent.

**Additional supporting reproducer 4 (later current version)**
The same family also appears when each outer row feeds a `MERGE` subquery that should return one scalar row per input:

```cypher
UNWIND range(1, 5) AS i
CALL {
  WITH i
  MERGE (n:Node {id: i})
    ON CREATE SET n.created = true
    ON MATCH SET n.matched = true
  RETURN n.id AS id
}
RETURN id
ORDER BY id;
```

Observed results:

- Neo4j:

```text
1
2
3
4
5
```

- FalkorDB `v4.18.3`:

```text
1
```

So the same root cause is not limited to relationship writes or pairwise outer rows. FalkorDB can collapse five independent outer rows down to a single surviving row after a write inside `CALL { ... }`.

## Verification environment
Verified using:
- plain FalkorDB Docker image
- RESP execution path from the local differential-testing harness

Supporting artifacts:
- [tmp_falkor_q2875_min_v1](D:/sci/works/get%20bugs/graph-bug-fuzz-mvp/data/stage45_runs/tmp_falkor_q2875_min_v1/summary.json)
- [tmp_falkor_q2875_min_v2](D:/sci/works/get%20bugs/graph-bug-fuzz-mvp/data/stage45_runs/tmp_falkor_q2875_min_v2/summary.json)
- [tmp_falkor_q2875_min_v3](D:/sci/works/get%20bugs/graph-bug-fuzz-mvp/data/stage45_runs/tmp_falkor_q2875_min_v3/summary.json)

**Stage3 provenance**
This report predates explicit file-level Stage3 provenance tracking.

Later current-version supporting cases were also observed in:

- Stage3 run: `historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b`
- Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_falkordb_4_18_2_skeleton_20260501`
- Query IDs:
  - `FalkorDB__FalkorDB__749__q001`
- Case IDs:
  - `case_83ef50dab0f2`

Later current-version supporting cases:

- Stage3 run: `historical_bug_queries_parallel_llm_v1_feature_only_random_20260506`
- Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_falkordb_4_18_3_feature_only_random_20260507`
- Query IDs:
  - `FalkorDB__FalkorDB__383__q002`
  - `FalkorDB__FalkorDB__1466__q005`
- Case IDs:
  - `case_15e172663904`
  - `case_87d8ad078e7c`
