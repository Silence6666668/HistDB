done
**Suggested title**
`List comprehension over collect(...) may return null when the expression uses an outer variable`

**FalkorDB version**
Observed on:

- FalkorDB `4.18.2`
- FalkorDB `4.18.4`

Docker images used locally:
- `falkordb/falkordb:v4.18.2`
- `falkordb/falkordb:v4.18.4`

**Discovered on**
- `2026-05-01`

**Environment**
- Host OS: Windows
- Architecture: x86_64
- Deployment: Docker
- Connection method: RESP / `GRAPH.QUERY`
- Client used for direct reproduction: repo executor + direct RESP execution
- Differential comparison target: Neo4j Community

**Describe the bug**
FalkorDB may return `null` inside a list comprehension over `collect(...)` when the list-expression combines the collected value with an outer variable.

In the minimized repro below, `collect(v.age)` is `[30]` and the current outer row has `n.age = 25`, so the list comprehension:

```cypher
[x IN collect(v.age) WHERE x > 20 | x + n.age]
```

should evaluate to:

```text
[55]
```

Neo4j returns `[55]`.
FalkorDB returns `[null]`.

This does not look like a general `collect(...)` problem or a general arithmetic problem. The failure starts when the list-comprehension expression combines the collected element `x` with the outer row variable `n.age`.

**To Reproduce**
1. Start the FalkorDB Docker container.
2. Run:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice', age:30})-[:KNOWS]->(:Person {name:'Bob', age:25});

MATCH (v:Person {name:'Alice'})--(n:Person)
RETURN n.age AS age,
       [x IN collect(v.age) WHERE x > 20 | x + n.age] AS vals
ORDER BY age;
```

**Expected behavior**
Neo4j returns:

```text
25, [55]
```

FalkorDB should return the same row.

**Actual behavior**
FalkorDB returns:

```text
25, [null]
```

So the collected element survives the filter, but the final expression `x + n.age` is evaluated as `null`.

**Control query 1**
If the list comprehension does not use the outer variable in the expression, both engines behave correctly:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice', age:30})-[:KNOWS]->(:Person {name:'Bob', age:25});

MATCH (v:Person {name:'Alice'})--(n:Person)
RETURN n.age AS age,
       [x IN collect(v.age) WHERE x > 20 | x] AS vals
ORDER BY age;
```

Observed result on both Neo4j and FalkorDB:

```text
25, [30]
```

So the issue is not that `collect(v.age)` itself is bad.

**Control query 2**
If the same logic is rewritten into rows first with `UNWIND`, both engines also behave correctly:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice', age:30})-[:KNOWS]->(:Person {name:'Bob', age:25});

MATCH (v:Person {name:'Alice'})--(n:Person)
WITH n, collect(v.age) AS vals
UNWIND vals AS x
WITH n, x
WHERE x > 20
RETURN n.age AS age, x + n.age AS val
ORDER BY age;
```

Observed result on both Neo4j and FalkorDB:

```text
25, 55
```

This makes the boundary much clearer: the failure is specific to the list-comprehension expression over `collect(...)`, not to the arithmetic `x + n.age` itself.

**Differential seed**
This family surfaced while minimizing:

- `neo4j__12868__q001`

**Verification Environment**
Plain FalkorDB Docker image is enough.

Preferred verification target:
- Docker image
- x86_64
- `falkordb/falkordb:v4.18.2`

I can verify a fix using:
- plain FalkorDB Docker image on Windows host
- project FalkorDB executor / direct RESP execution

**Stage3 provenance**
- Stage3 run: `historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b`
- Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_falkordb_4_18_2_skeleton_20260501`
- Query ID: `neo4j__12868__q001`
- Case ID: `case_632340c5b8a4`
