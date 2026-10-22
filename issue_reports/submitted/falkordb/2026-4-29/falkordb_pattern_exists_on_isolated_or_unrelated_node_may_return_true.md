done
**Suggested title**
`Pattern EXISTS on an isolated or unrelated node may return true`

**FalkorDB version**
Observed on:

- FalkorDB `4.18.1`
- FalkorDB `4.18.2`

Docker image used locally:
- `falkordb/falkordb:latest`
- `falkordb/falkordb:v4.18.2`

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
FalkorDB may evaluate a simple pattern-existence predicate like:

```cypher
EXISTS((n)-[:KNOWS]->())
```

to `true` even when the current bound node has no outgoing `[:KNOWS]` relationship at all.

In the minimized repro below, the graph contains only three isolated `:Person` nodes and no relationships. Neo4j correctly returns `false` for all rows. FalkorDB returns `true` for all rows.

This suggests FalkorDB is not evaluating the predicate against the current bound node correctly.

**To Reproduce**
1. Start the FalkorDB Docker container.
2. Run:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {id: 1}), (:Person {id: 2}), (:Person {id: 3});

MATCH (n:Person)
RETURN n.id AS id, EXISTS((n)-[:KNOWS]->()) AS has_relationship
ORDER BY id;
```

**Expected behavior**
Neo4j returns:

```text
1, false
2, false
3, false
```

FalkorDB should return the same rows.

**Actual behavior**
FalkorDB returns:

```text
1, true
2, true
3, true
```

So the existence predicate reports an outgoing `[:KNOWS]` match even though the graph contains no such relationships at all.

The same minimal repro was re-checked directly on FalkorDB `4.18.2` and still returns:

```text
1, true
2, true
3, true
```

**Control query 1**
The same graph shape behaves correctly with a normal `OPTIONAL MATCH` plus `count`:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {id: 1}), (:Person {id: 2}), (:Person {id: 3});

MATCH (n:Person)
OPTIONAL MATCH (n)-[:KNOWS]->(m)
RETURN n.id AS id, count(m) AS rel_count
ORDER BY id;
```

Observed result on both Neo4j and FalkorDB:

```text
1, 0
2, 0
3, 0
```

So the graph is genuinely edge-free, and the mismatch is specific to the `EXISTS((n)-[:KNOWS]->())` predicate rather than to all topology reads.

**Control query 2**
If the graph contains one real `[:KNOWS]` edge, Neo4j reports `true` only for the source node, but FalkorDB still returns `true` for every row:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Person {id: 1}), (b:Person {id: 2}), (c:Person {id: 3});
CREATE (a)-[:KNOWS]->(b);

MATCH (n:Person)
RETURN n.id AS id, EXISTS((n)-[:KNOWS]->()) AS has_relationship
ORDER BY id;
```

Observed results:

- Neo4j:

```text
1, true
2, false
3, false
```

- FalkorDB:

```text
1, true
2, true
3, true
```

This makes the boundary sharper: the issue is not just “true on empty graph”, but more generally that the predicate appears disconnected from the current bound node.

**Differential seed**
This family was first noticed in:

- `RedisGraph__RedisGraph__2569__q001`

**Additional supporting reproducer**
The same family also appears on FalkorDB `4.18.2` with a variable-length incoming pattern:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice'}),
       (:Person {name:'Bob'}),
       (:Person {name:'Charlie'});
MATCH (a:Person {name:'Alice'}), (b:Person {name:'Bob'})
CREATE (a)-[:KNOWS]->(b);

MATCH (n:Person)
RETURN EXISTS((n)<-[:KNOWS*1..2]-(:Person)) AS has_path
ORDER BY n.name;
```

Observed results:

- Neo4j:

```text
false
true
true
```

- FalkorDB `4.18.2`:

```text
true
true
true
```

So the bad behavior is not limited to a fixed-length one-hop outgoing pattern. The same predicate disconnection also survives on a variable-length incoming pattern.

**Additional supporting reproducer 2 (current version)**
The same disconnection also appears when the pattern is used as a bare `WHERE` predicate without an explicit `EXISTS(...)` wrapper:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name: 'Alice'})-[:KNOWS {since: 2020}]->(:City {name: 'Paris'});

MATCH (b:City)
WHERE (:Person {name: 'Alice'})-->(b)<--(:Person {name: 'Alice'})
RETURN b.name AS name;
```

Observed results:

- Neo4j:

```text
0 rows
```

- FalkorDB `4.18.2`:

```text
Paris
```

So FalkorDB accepts a two-sided pattern predicate that should be false for `Paris`, but still keeps the row.

**Control for supporting reproducer 2**
The same bad truth value is visible if the predicate is projected explicitly:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name: 'Alice'})-[:KNOWS {since: 2020}]->(:City {name: 'Paris'});

MATCH (b:City)
RETURN b.name AS name,
       EXISTS((:Person {name: 'Alice'})-->(b)<--(:Person {name: 'Alice'})) AS ok;
```

Observed results:

- Neo4j:

```text
Paris, false
```

- FalkorDB `4.18.2`:

```text
Paris, true
```

This keeps the boundary consistent with the main bug: the problem is not just row filtering syntax in `WHERE`; FalkorDB is computing the underlying pattern-existence truth value incorrectly.

**Related note**
This may also explain why some other FalkorDB seeds involving `EXISTS((a)-[:REL]->(b))` after writes or deletes behave as if a path is still visible when it should not be.

**Verification Environment**
Plain FalkorDB Docker image is enough.

Preferred verification target:
- Docker image
- x86_64
- `falkordb/falkordb`

I can verify a fix using:
- plain FalkorDB Docker image on Windows host
- direct `redis-cli GRAPH.QUERY`

**Stage3 provenance**
This report predates explicit file-level Stage3 provenance tracking.

Later current-version supporting case:

- Stage3 run: `historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b`
- Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_falkordb_4_18_2_skeleton_20260501`
- Query ID: `FalkorDB__FalkorDB__1248__q002`
- Case ID: `case_da106f2e68d6`

Additional current-version supporting case:

- Stage3 run: `historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b`
- Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_falkordb_4_18_2_skeleton_20260501`
- Query ID: `RedisGraph__RedisGraph__1268__q001`
- Case ID: `case_cf7074d40294`
