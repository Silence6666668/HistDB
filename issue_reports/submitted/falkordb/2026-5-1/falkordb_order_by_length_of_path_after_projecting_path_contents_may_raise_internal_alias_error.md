done
**Suggested title**
`ORDER BY length(path) after projecting path contents may raise an internal alias-resolution error`

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
- Differential comparison target: Neo4j Community `2026.02.2`

**Describe the bug**
FalkorDB may raise an internal alias-resolution error when a query projects path-derived content such as `nodes(p)` and then orders the result by `length(p)` without first materializing the length into a scalar alias.

In the minimized repro below, Neo4j returns the names along the unique 3-hop path from `Alice` to `Diana`.
FalkorDB instead fails with:

```text
_AR_EXP_UpdateEntityIdx: Unable to locate a value with alias p within the record
```

This does not look like a general path-binding problem, because FalkorDB can return `p`, `nodes(p)`, and `length(p)` directly. The failure starts when the query both:

- projects path contents, and
- orders by a raw `length(p)` expression in the same `RETURN`.

**To Reproduce**
1. Start the FalkorDB Docker container.
2. Run:

```cypher
CREATE (a:Person {name: 'Alice'}),
       (b:Person {name: 'Bob'}),
       (c:Person {name: 'Charlie'}),
       (d:Person {name: 'Diana'}),
       (a)-[:KNOWS]->(b),
       (b)-[:KNOWS]->(c),
       (c)-[:KNOWS]->(d),
       (a)-[:KNOWS]->(c);

MATCH (s:Person {name:'Alice'}), (t:Person {name:'Diana'})
WITH s, t
MATCH p = (s)-[*3]->(t)
RETURN [x IN nodes(p) | x.name] AS names
ORDER BY length(p) DESC;
```

**Expected behavior**
Observed Neo4j result:

```text
["Alice", "Bob", "Charlie", "Diana"]
```

FalkorDB should return the same row.

**Actual behavior**
Observed FalkorDB result:

```text
_AR_EXP_UpdateEntityIdx: Unable to locate a value with alias p within the record
```

So FalkorDB loses the path alias while evaluating the `ORDER BY length(p)` expression after the projection.

**Control query 1**
If the same projection is returned without `ORDER BY`, both engines behave correctly:

```cypher
CREATE (a:Person {name: 'Alice'}),
       (b:Person {name: 'Bob'}),
       (c:Person {name: 'Charlie'}),
       (d:Person {name: 'Diana'}),
       (a)-[:KNOWS]->(b),
       (b)-[:KNOWS]->(c),
       (c)-[:KNOWS]->(d),
       (a)-[:KNOWS]->(c);

MATCH (s:Person {name:'Alice'}), (t:Person {name:'Diana'})
WITH s, t
MATCH p = (s)-[*3]->(t)
RETURN [x IN nodes(p) | x.name] AS names;
```

Observed result on both Neo4j and FalkorDB:

```text
["Alice", "Bob", "Charlie", "Diana"]
```

So the path projection itself is not the problem.

**Control query 2**
If the query orders by a positional column instead of `length(p)`, both engines also behave correctly:

```cypher
CREATE (a:Person {name: 'Alice'}),
       (b:Person {name: 'Bob'}),
       (c:Person {name: 'Charlie'}),
       (d:Person {name: 'Diana'}),
       (a)-[:KNOWS]->(b),
       (b)-[:KNOWS]->(c),
       (c)-[:KNOWS]->(d),
       (a)-[:KNOWS]->(c);

MATCH (s:Person {name:'Alice'}), (t:Person {name:'Diana'})
WITH s, t
MATCH p = (s)-[*3]->(t)
RETURN [x IN nodes(p) | x.name] AS names
ORDER BY 1 DESC;
```

Observed result on both Neo4j and FalkorDB:

```text
["Alice", "Bob", "Charlie", "Diana"]
```

So ordering in general is not broken.

**Control query 3**
If `length(p)` is first materialized into an alias, FalkorDB also behaves normally:

```cypher
CREATE (a:Person {name: 'Alice'}),
       (b:Person {name: 'Bob'}),
       (c:Person {name: 'Charlie'}),
       (d:Person {name: 'Diana'}),
       (a)-[:KNOWS]->(b),
       (b)-[:KNOWS]->(c),
       (c)-[:KNOWS]->(d),
       (a)-[:KNOWS]->(c);

MATCH (s:Person {name:'Alice'}), (t:Person {name:'Diana'})
WITH s, t
MATCH p = (s)-[*3]->(t)
RETURN [x IN nodes(p) | x.name] AS names, length(p) AS len
ORDER BY len DESC;
```

Observed result on both Neo4j and FalkorDB:

```text
["Alice", "Bob", "Charlie", "Diana"], 3
```

This makes the boundary much clearer: the failure is tied specifically to raw `ORDER BY length(p)` after path-content projection, not to ordering by path length in general.

**Control query 4**
Returning the path value itself with the same `ORDER BY` works on both engines:

```cypher
CREATE (a:Person {name: 'Alice'}),
       (b:Person {name: 'Bob'}),
       (c:Person {name: 'Charlie'}),
       (d:Person {name: 'Diana'}),
       (a)-[:KNOWS]->(b),
       (b)-[:KNOWS]->(c),
       (c)-[:KNOWS]->(d),
       (a)-[:KNOWS]->(c);

MATCH (s:Person {name:'Alice'}), (t:Person {name:'Diana'})
WITH s, t
MATCH p = (s)-[*3]->(t)
RETURN p
ORDER BY length(p) DESC;
```

Observed result on both Neo4j and FalkorDB:

```text
the Alice->Bob->Charlie->Diana path
```

So the bug is not simply that `ORDER BY length(p)` is unsupported; it is the combination with projected path contents that triggers the alias-resolution failure.

**Differential seed**
This family surfaced while minimizing:

- `FalkorDB__FalkorDB__367__q003`

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
- Query ID: `FalkorDB__FalkorDB__367__q003`
- Case ID: `case_68c5af3e98d0`
