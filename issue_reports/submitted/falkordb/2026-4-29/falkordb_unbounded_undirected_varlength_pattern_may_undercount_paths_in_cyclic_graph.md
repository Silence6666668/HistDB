**Suggested title**
`Unbounded undirected variable-length pattern may undercount paths in a cyclic graph`

**FalkorDB version**
Observed on:

- FalkorDB `4.18.1`

Docker image used locally:
- `falkordb/falkordb:latest`

**Discovered on**
- `2026-04-23`

**Environment**
- Host OS: Windows
- Architecture: x86_64
- Deployment: Docker
- Connection method: RESP / `GRAPH.QUERY`
- Client used for direct reproduction: direct `redis-cli GRAPH.QUERY`
- Differential comparison target: Neo4j Community `2026.02.2`

**Describe the bug**
FalkorDB may undercount matches for an **unbounded undirected variable-length relationship pattern** in a cyclic graph, even when the equivalent bounded pattern returns the expected count.

In the minimized repro below, the graph has three people and four `[:FRIEND_OF]` edges:

- `Alice -> Bob`
- `Alice -> Charlie`
- `Bob -> Charlie`
- `Charlie -> Alice`

On that graph, Neo4j reports `20` matching paths from `Charlie` through the undirected unbounded pattern:

```cypher
MATCH p = (:Person {name: 'Charlie'})-[:FRIEND_OF*]-(other:Person)
RETURN count(p) AS pathCount, count(DISTINCT other) AS distinctOthers;
```

FalkorDB returns only `12`, while still agreeing that `3` distinct endpoints are reachable.

This suggests the problem is not endpoint reachability, but path enumeration under unbounded undirected expansion.

**To Reproduce**
1. Start the FalkorDB Docker container.
2. Run:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Person {name: 'Alice'}),
       (b:Person {name: 'Bob'}),
       (c:Person {name: 'Charlie'}),
       (a)-[:FRIEND_OF]->(b),
       (a)-[:FRIEND_OF]->(c),
       (b)-[:FRIEND_OF]->(c),
       (c)-[:FRIEND_OF]->(a);

MATCH p = (:Person {name: 'Charlie'})-[:FRIEND_OF*]-(other:Person)
RETURN count(p) AS pathCount, count(DISTINCT other) AS distinctOthers;
```

**Expected behavior**
Neo4j returns:

```text
20, 3
```

FalkorDB should return the same result.

**Actual behavior**
FalkorDB returns:

```text
12, 3
```

So FalkorDB undercounts the number of matched paths while still agreeing on the set of reachable endpoints.

**Control query 1**
If the graph is reduced to a simple 3-edge cycle with no extra shortcut edge, both engines agree on the unbounded count:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Person {name: 'Alice'}),
       (b:Person {name: 'Bob'}),
       (c:Person {name: 'Charlie'}),
       (a)-[:FRIEND_OF]->(b),
       (b)-[:FRIEND_OF]->(c),
       (c)-[:FRIEND_OF]->(a);

MATCH p = (:Person {name: 'Charlie'})-[:FRIEND_OF*]-(other:Person)
RETURN count(p) AS pathCount, count(DISTINCT other) AS distinctOthers;
```

Observed result on both Neo4j and FalkorDB:

```text
6, 3
```

So unbounded undirected variable-length matching is not broken in general.

**Control query 2**
On the same 4-edge graph from the minimized repro, the bounded equivalent behaves correctly on both engines:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Person {name: 'Alice'}),
       (b:Person {name: 'Bob'}),
       (c:Person {name: 'Charlie'}),
       (a)-[:FRIEND_OF]->(b),
       (a)-[:FRIEND_OF]->(c),
       (b)-[:FRIEND_OF]->(c),
       (c)-[:FRIEND_OF]->(a);

MATCH p = (:Person {name: 'Charlie'})-[:FRIEND_OF*1..4]-(other:Person)
RETURN count(p) AS pathCount, count(DISTINCT other) AS distinctOthers;
```

Observed result on both Neo4j and FalkorDB:

```text
20, 3
```

This makes the boundary much sharper: the mismatch appears tied specifically to the **unbounded** undirected form `*`, not to the bounded `*1..4` equivalent on the same graph.

**Control query 3**
The same mismatch appears whether the query counts bound paths or bound relationship lists:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Person {name: 'Alice'}),
       (b:Person {name: 'Bob'}),
       (c:Person {name: 'Charlie'}),
       (a)-[:FRIEND_OF]->(b),
       (a)-[:FRIEND_OF]->(c),
       (b)-[:FRIEND_OF]->(c),
       (c)-[:FRIEND_OF]->(a);

MATCH (:Person {name: 'Charlie'})-[r:FRIEND_OF*]-(other:Person)
RETURN count(r) AS degree, count(DISTINCT other) AS distinctOthers;
```

Observed results:

- Neo4j: `20, 3`
- FalkorDB: `12, 3`

So this is not just a `count(path)` rendering issue; FalkorDB is also undercounting the underlying variable-length matches when the relationship list variable is bound directly.

**Why this matters**
This can silently change aggregates and downstream filtering for queries that rely on unbounded undirected variable-length expansion in cyclic graphs. The affected queries are not exotic: they use only `MATCH`, a standard variable-length pattern, and simple aggregation.

**Differential seed**
This family was first noticed in:

- `RedisGraph__RedisGraph__1230__q002`

**Verification Environment**
Plain FalkorDB Docker image is enough.

Preferred verification target:
- Docker image
- x86_64
- `falkordb/falkordb`

I can verify a fix using:
- plain FalkorDB Docker image on Windows host
- direct `redis-cli GRAPH.QUERY`
