**Suggested title**
`count(*)` may undercount parallel edges of different relationship types between the same endpoints

**FalkorDB version**
Originally minimized on:

- FalkorDB `4.16.8`

Also reproduced later on:

- FalkorDB `4.18.1`
- FalkorDB `4.18.2`

Docker image used locally:
- `falkordb/falkordb-server:latest`
- `falkordb/falkordb:latest`
- `falkordb/falkordb:v4.18.2`

**Environment**
- Host OS: Windows
- Architecture: x86_64
- Deployment: Docker
- Connection method: RESP / `GRAPH.QUERY`
- Client used for direct reproduction: project FalkorDB executor
- Differential comparison target: Neo4j Community `2026.03.1`

**Describe the bug**
FalkorDB appears to undercount matches when the same two nodes are connected by two different relationship types and the query aggregates with `count(*)`.

With the minimal graph:

```cypher
CREATE (a:Person {name: 'Alice'}),
       (b:Person {name: 'Bob'}),
       (a)-[:FRIENDS_WITH]->(b),
       (a)-[:WORKS_WITH]->(b);
```

the query:

```cypher
MATCH (p:Person)
WHERE p.name = 'Alice'
MATCH (p)-[:FRIENDS_WITH|WORKS_WITH]->(friend)
WITH friend, count(*) AS connectionCount
RETURN friend.name AS friendName, connectionCount
ORDER BY friendName
```

returns:

- Neo4j: `Bob, 2`
- FalkorDB: `Bob, 1`

This looks like FalkorDB is deduplicating matched rows by endpoint pair instead of counting both matching relationships.

**To Reproduce**
1. Start a FalkorDB Docker container.
2. Run:

```cypher
MATCH (n) DETACH DELETE n;

CREATE (a:Person {name: 'Alice'}),
       (b:Person {name: 'Bob'}),
       (a)-[:FRIENDS_WITH]->(b),
       (a)-[:WORKS_WITH]->(b);

MATCH (p:Person)
WHERE p.name = 'Alice'
MATCH (p)-[:FRIENDS_WITH|WORKS_WITH]->(friend)
WITH friend, count(*) AS connectionCount
RETURN friend.name AS friendName, connectionCount
ORDER BY friendName
```

**Expected behavior**
The result should be:

```text
friendName = Bob
connectionCount = 2
```

There are two distinct matching relationships from `Alice` to `Bob`:
- `[:FRIENDS_WITH]`
- `[:WORKS_WITH]`

This is the result returned by Neo4j `2026.03.1`.

**Actual behavior**
FalkorDB returns:

```text
friendName = Bob
connectionCount = 1
```

**Control cases**
Two nearby control checks help isolate the issue:

1. Single-relationship control:

```cypher
MATCH (n) DETACH DELETE n;

CREATE (a:Person {name: 'Alice'}),
       (b:Person {name: 'Bob'}),
       (a)-[:FRIENDS_WITH]->(b);

MATCH (p:Person)
WHERE p.name = 'Alice'
MATCH (p)-[:FRIENDS_WITH|WORKS_WITH]->(friend)
WITH friend, count(*) AS connectionCount
RETURN friend.name AS friendName, connectionCount
ORDER BY friendName
```

Observed result on both Neo4j and FalkorDB:

```text
friendName = Bob
connectionCount = 1
```

2. Adding `WHERE connectionCount > 0` does not change the divergence:

```cypher
MATCH (n) DETACH DELETE n;

CREATE (a:Person {name: 'Alice'}),
       (b:Person {name: 'Bob'}),
       (a)-[:FRIENDS_WITH]->(b),
       (a)-[:WORKS_WITH]->(b);

MATCH (p:Person)
WHERE p.name = 'Alice'
MATCH (p)-[:FRIENDS_WITH|WORKS_WITH]->(friend)
WITH friend, count(*) AS connectionCount
WHERE connectionCount > 0
RETURN friend.name AS friendName, connectionCount
ORDER BY friendName
```

Observed results:
- Neo4j: `Bob, 2`
- FalkorDB: `Bob, 1`

This suggests the issue is not caused by the filter, but by how FalkorDB counts matched rows for parallel edges of different types.

**Additional supporting reproducer**
The same undercount still reproduces on FalkorDB `4.18.2` with a different pair of relationship types:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Person {name:'Alice'}),
       (b:Person {name:'Bob'}),
       (a)-[:KNOWS]->(b),
       (a)-[:LIKES]->(b);

MATCH (p:Person)
WHERE p.name = 'Alice'
MATCH (p)-[:KNOWS|LIKES]->(friend)
WITH friend, count(*) AS connectionCount
RETURN friend.name AS friendName, connectionCount
ORDER BY friendName;
```

Observed results:

- Neo4j:

```text
Bob, 2
```

- FalkorDB `4.18.2`:

```text
Bob, 1
```

So the family is not tied specifically to `FRIENDS_WITH|WORKS_WITH`. The same endpoint-pair undercount survives on the current version with `KNOWS|LIKES`.

**Additional context**
This was originally found in a larger `UNION ALL` query, but it still reproduces after removing:
- the second `UNION` branch
- `ORDER BY` dependencies from the original query shape
- unrelated setup data

So the reproducer above appears to capture the core issue directly.

**Verification Environment**
Plain FalkorDB Docker image is enough.

Preferred verification target:
- Docker image
- x86_64
- `falkordb/falkordb-server`

I can verify a fix using:
- plain FalkorDB Docker image on Windows host
- project RESP executor / direct `GRAPH.QUERY`

**Stage3 provenance**
This report predates explicit file-level Stage3 provenance tracking.

Later current-version supporting case:

- Stage3 run: `historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b`
- Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_falkordb_4_18_2_skeleton_20260501`
- Query ID: `FalkorDB__FalkorDB__1848__q003`
- Case ID: `case_cfcaf7cc330f`
