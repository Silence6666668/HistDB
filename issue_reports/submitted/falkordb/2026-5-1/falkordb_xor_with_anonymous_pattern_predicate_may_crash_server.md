**Suggested title**
`XOR` with an anonymous pattern predicate may crash the server

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
FalkorDB may crash when a boolean `XOR` expression combines an anonymous pattern predicate with another boolean condition.

In the minimized repro below, the graph contains one `KNOWS` relationship, so the predicate:

```cypher
()-[:KNOWS]-() XOR n.name = 'Alice'
```

is a normal boolean expression that Neo4j evaluates row by row.

Neo4j returns the row for `Bob`.
FalkorDB instead closes the connection and the container exits with code `139`.

This does not look like a generic anonymous-pattern problem and does not look like a generic `XOR` problem:

- `true XOR n.name = 'Alice'` works on FalkorDB
- `()-[:KNOWS]-() OR n.name = 'Alice'` works on FalkorDB
- `()-[:KNOWS]-() AND n.name = 'Alice'` works on FalkorDB

The crash appears specifically when `XOR` is used with an anonymous pattern predicate.

**To Reproduce**
1. Start the FalkorDB Docker container.
2. Run:

```cypher
CREATE (:Person {name:'Alice'}), (:Person {name:'Bob'});
MATCH (a:Person {name:'Alice'}), (b:Person {name:'Bob'})
CREATE (a)-[:KNOWS]->(b);

MATCH (n)
WHERE ()-[:KNOWS]-() XOR n.name = 'Alice'
RETURN n.name
LIMIT 5;
```

**Expected behavior**
Observed Neo4j result:

```text
Bob
```

FalkorDB should return the same row and remain healthy.

**Actual behavior**
Observed FalkorDB result:

```text
FalkorDB connection closed unexpectedly.
```

After the repro, the Docker container exits with:

```text
exited 139
```

So this is a server crash, not a normal query error.

**Control query 1**
`XOR` itself works when no anonymous pattern predicate is involved:

```cypher
CREATE (:Person {name:'Alice'}), (:Person {name:'Bob'});
MATCH (a:Person {name:'Alice'}), (b:Person {name:'Bob'})
CREATE (a)-[:KNOWS]->(b);

MATCH (n)
WHERE true XOR n.name = 'Alice'
RETURN n.name
LIMIT 5;
```

Observed result on both Neo4j and FalkorDB:

```text
Bob
```

So the problem is not a generic `XOR` failure.

**Control query 2**
The same anonymous pattern predicate works with `OR`:

```cypher
CREATE (:Person {name:'Alice'}), (:Person {name:'Bob'});
MATCH (a:Person {name:'Alice'}), (b:Person {name:'Bob'})
CREATE (a)-[:KNOWS]->(b);

MATCH (n)
WHERE ()-[:KNOWS]-() OR n.name = 'Alice'
RETURN n.name
LIMIT 5;
```

Observed result on both Neo4j and FalkorDB:

```text
Alice
Bob
```

**Control query 3**
The same anonymous pattern predicate also works with `AND`:

```cypher
CREATE (:Person {name:'Alice'}), (:Person {name:'Bob'});
MATCH (a:Person {name:'Alice'}), (b:Person {name:'Bob'})
CREATE (a)-[:KNOWS]->(b);

MATCH (n)
WHERE ()-[:KNOWS]-() AND n.name = 'Alice'
RETURN n.name
LIMIT 5;
```

Observed result on both Neo4j and FalkorDB:

```text
Alice
```

So anonymous pattern predicates are not broken in general.

**Control query 4**
The same crash family also appears after `WITH`:

```cypher
CREATE (:Person {name:'Alice'}), (:Person {name:'Bob'});
MATCH (a:Person {name:'Alice'}), (b:Person {name:'Bob'})
CREATE (a)-[:KNOWS]->(b);

MATCH (n)
WITH n
WHERE ()-[:KNOWS]-() XOR n.name = 'Alice'
RETURN n.name
LIMIT 5;
```

Observed behavior:

- Neo4j returns `Bob`
- FalkorDB again closes the connection and exits with code `139`

So the issue is not limited to one specific query shape around the `WHERE` clause.

**Control query 5**
The same crash family also appears under `OPTIONAL MATCH`:

```cypher
CREATE (:Person {name:'Alice'}), (:Person {name:'Bob'});
MATCH (a:Person {name:'Alice'}), (b:Person {name:'Bob'})
CREATE (a)-[:KNOWS]->(b);

OPTIONAL MATCH (a)--(b)
WHERE ()--() XOR a.name = 'Alice'
RETURN a, b;
```

Observed behavior:

- Neo4j returns one row
- FalkorDB closes the connection and exits with code `139`

This suggests the deeper issue is the `XOR` combination itself, not one particular surrounding clause.

**Differential seeds**
This family surfaced while minimizing:

- `FalkorDB__FalkorDB__271__q003`

Supporting current-version cases:

- `FalkorDB__FalkorDB__1466__q004`
- `FalkorDB__FalkorDB__1466__q006`

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
- Query ID: `FalkorDB__FalkorDB__271__q003`
- Case ID: `case_682eda0a63bb`
- Supporting seeds:
  - `FalkorDB__FalkorDB__1466__q004` / `case_26bed2683ce2`
  - `FalkorDB__FalkorDB__1466__q006` / `case_163b4a7efb77`
