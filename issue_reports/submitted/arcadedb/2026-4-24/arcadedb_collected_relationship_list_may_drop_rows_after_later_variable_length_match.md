done

**Suggested title**
`Collected relationship list may drop all rows after a later variable-length match`

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
ArcadeDB may drop all rows when a relationship variable is first aggregated into a list with `collect(...)`, and a later `MATCH` in the same query uses a variable-length relationship pattern from the same anchor node.

The failure is specific to collecting the relationship objects themselves.
Equivalent scalar aggregates such as `count(r)`, `collect(type(r))`, and `collect(r.since)` behave normally.

**To Reproduce**

Setup:

```cypher
CREATE (a:Person {name:'Alice', age:30}),
       (b:Person {name:'Bob', age:25}),
       (c:Person {name:'Charlie', age:35}),
       (a)-[:KNOWS {since:2020}]->(b),
       (b)-[:KNOWS {since:2019}]->(c);
```

Query:

```cypher
MATCH (a:Person {name:'Alice'})-[r:KNOWS]->(b:Person)
WITH a, collect(r) AS rels
MATCH path = (a)-[:KNOWS*1..2]->(c:Person)
RETURN length(path) AS len, size(rels) AS relationCount
ORDER BY len;
```

**Expected behavior**
Neo4j returns:

```text
len = 1, relationCount = 1
len = 2, relationCount = 1
```

ArcadeDB should return the same two rows.

**Actual behavior**
ArcadeDB returns:

```text
0 rows
```

**Control cases**

If the earlier relationship binding is aggregated with `count(r)` instead of `collect(r)`, ArcadeDB behaves correctly:

```cypher
MATCH (a:Person {name:'Alice'})-[r:KNOWS]->(b:Person)
WITH a, count(r) AS rc
MATCH path = (a)-[:KNOWS*1..2]->(c:Person)
RETURN length(path) AS len, rc
ORDER BY len;
```

Observed result on both Neo4j and ArcadeDB:

```text
len = 1, rc = 1
len = 2, rc = 1
```

If the earlier aggregation collects scalar values derived from the relationship rather than the relationship objects themselves, ArcadeDB also behaves correctly:

```cypher
MATCH (a:Person {name:'Alice'})-[r:KNOWS]->(b:Person)
WITH a, collect(type(r)) AS relTypes
MATCH path = (a)-[:KNOWS*1..2]->(c:Person)
RETURN length(path) AS len, relTypes
ORDER BY len;
```

Observed result on both Neo4j and ArcadeDB:

```text
len = 1, relTypes = ['KNOWS']
len = 2, relTypes = ['KNOWS']
```

This also works with:

```cypher
collect(r.since)
```

So the problem appears to be tied specifically to carrying a collected list of relationship objects.

If the later match is fixed-length instead of variable-length, ArcadeDB behaves correctly again:

```cypher
MATCH (a:Person {name:'Alice'})-[r:KNOWS]->(b:Person)
WITH a, collect(r) AS rels
MATCH path = (a)-[:KNOWS]->(c:Person)
RETURN length(path) AS len, size(rels) AS relationCount
ORDER BY len;
```

Observed result on both Neo4j and ArcadeDB:

```text
len = 1, relationCount = 1
```

So the boundary is:

- `collect(r)` or `collect(DISTINCT r)` is carried forward
- a later `MATCH` uses a variable-length relationship pattern
- ArcadeDB drops all rows

while:

- `count(r)` works
- `collect(type(r))` works
- `collect(r.since)` works
- a fixed-length later match works

**Stronger variant**
The problem also appears when the earlier relationship comes from `OPTIONAL MATCH`:

```cypher
MATCH (a:Person {name:'Alice'})
OPTIONAL MATCH (a)-[r:KNOWS]->(b:Person)
WITH a, collect(DISTINCT r) AS rels
MATCH path = (a)-[:KNOWS*1..2]->(c:Person)
RETURN length(path) AS len, size(rels) AS relationCount
ORDER BY len;
```

Observed Neo4j result:

```text
len = 1, relationCount = 1
len = 2, relationCount = 1
```

Observed ArcadeDB result:

```text
0 rows
```

So `OPTIONAL MATCH` is not required for the failure, but it does not avoid it either.

**Differential seed**
This family was first noticed in the latest Neo4j-vs-ArcadeDB differential run from:

- `FalkorDB__FalkorDB__349__q002`

**Verification environment**
Verified directly against:

- Neo4j Docker container: `neo4j:latest`
- ArcadeDB Docker container: `arcadedata/arcadedb:latest`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.3.2`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.4.1-SNAPSHOT`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.4.2`
- Local differential-testing harness
- Direct ArcadeDB HTTP execution using the same request mode as Studio
