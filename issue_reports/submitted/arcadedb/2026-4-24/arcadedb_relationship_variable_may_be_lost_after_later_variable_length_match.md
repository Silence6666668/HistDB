done
**Suggested title**
`Relationship variable may be lost after a later variable-length match`

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
ArcadeDB may lose a previously bound relationship variable after a later `MATCH` uses a variable-length relationship pattern over the same endpoints.

In the repro below, `r` is already bound by the first `MATCH`.
Neo4j keeps that binding alive and `count(r)` remains `1`.
ArcadeDB returns `0`, as if `r` had become unbound after the later variable-length match.

**To Reproduce**

Setup:

```cypher
CREATE (a:Person {name:'Alice'}),
       (b:Person {name:'Bob'}),
       (a)-[:KNOWS]->(b);
```

Query:

```cypher
MATCH (a:Person {name:'Alice'})-[r:KNOWS]->(b:Person {name:'Bob'})
WITH a, b, r
MATCH path = (a)-[:KNOWS*]->(b)
RETURN count(r) AS rc;
```

**Expected behavior**
`r` is already bound to the `KNOWS` relationship, so the count should stay `1`.

Observed Neo4j result:

```text
rc = 1
```

**Actual behavior**
ArcadeDB returns:

```text
rc = 0
```

So the later variable-length match appears to invalidate or drop the carried relationship variable.

**Control cases**

If the later match is fixed-length instead of variable-length, ArcadeDB behaves correctly:

```cypher
MATCH (a:Person {name:'Alice'})-[r:KNOWS]->(b:Person {name:'Bob'})
WITH a, b, r
MATCH path = (a)-[:KNOWS]->(b)
RETURN count(r) AS rc;
```

Observed result on both Neo4j and ArcadeDB:

```text
rc = 1
```

The problem also reproduces with explicit variable-length bounds:

```cypher
MATCH (a:Person {name:'Alice'})-[r:KNOWS]->(b:Person {name:'Bob'})
WITH a, b, r
MATCH path = (a)-[:KNOWS*1..1]->(b)
RETURN count(r) AS rc;
```

Observed results:

- Neo4j: `rc = 1`
- ArcadeDB: `rc = 0`

This suggests the issue is not about arbitrary path length expansion, but about the variable-length relationship-pattern form itself.

ArcadeDB also loses the relationship variable when the query tries to collect it:

```cypher
MATCH (a:Person {name:'Alice'})-[r:KNOWS]->(b:Person {name:'Bob'})
WITH a, b, r
MATCH path = (a)-[:KNOWS*]->(b)
RETURN count(r) AS rc, collect(type(r)) AS rts;
```

Observed results:

- Neo4j: `rc = 1`, `rts = ['KNOWS']`
- ArcadeDB: `rc = 0`, `rts = []`

**Stronger variant**
The same problem appears even when `r` was created earlier in the same query:

```cypher
MATCH (a:Person {name:'Alice'}), (b:Person {name:'Bob'})
CREATE (a)-[r:KNOWS]->(b)
WITH a, b, r
MATCH path = (a)-[:KNOWS*]->(b)
WITH count(r) AS rc
RETURN rc;
```

Observed results:

- Neo4j: `rc = 1`
- ArcadeDB: `rc = 0`

That makes the failure mode more general: a carried relationship variable can disappear after a later variable-length match over the same endpoints, regardless of whether that relationship came from setup data or was created in the same statement.

An even stronger variant, closer to the original differential seed, can make ArcadeDB drop the row entirely rather than only zeroing out `r`:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice'}), (:Person {name:'Bob'});

MATCH (p1:Person), (p2:Person)
WHERE p1.name = 'Alice' AND p2.name = 'Bob'
MERGE (p1)-[r:KNOWS {since: 2020}]->(p2)
WITH p1, p2, r
MATCH path = (p1)-[:KNOWS*]->(p2)
RETURN p1.name AS start_name,
       p2.name AS end_name,
       COUNT(r) AS relationship_count,
       LAST(RELATIONSHIPS(path)) AS last_relationship;
```

Observed Neo4j result:

```text
Alice, Bob, 1, (Alice)-[:KNOWS]->(Bob)
```

Observed ArcadeDB result on all tested versions:

```text
0 rows
```

So in this shape, the later variable-length match does not just empty out the carried relationship variable. It can collapse the whole result row once the query also tries to consume the matched `path`.

This makes the boundary sharper:

- if the later match is fixed-length, ArcadeDB behaves correctly
- if the relationship already exists before the statement, ArcadeDB behaves correctly
- if the relationship is created or merged earlier in the same statement and a later variable-length match is used over the same endpoints, ArcadeDB can lose `r` or even lose the whole row

**Differential seed**
This family was first noticed in the latest Neo4j-vs-ArcadeDB differential run from:

- `FalkorDB__FalkorDB__1786__q003`

It also resurfaced in the later skeleton differential run in a cleaner count-only shape:

- `ArcadeData__arcadedb__3128__q002`

That later seed reduces to:

```cypher
UNWIND [{source_id: 1, target_id: 2}] AS row
MATCH (a:Node {id: row.source_id}), (b:Node {id: row.target_id})
MERGE (a)-[r:KNOWS]->(b)
ON CREATE SET r.since = 2020
ON MATCH SET r.updated = true
WITH a, b, r
MATCH path = (a)-[:KNOWS*1..2]-(b)
RETURN count(path) AS pathCount;
```

Observed results:

- Neo4j: `pathCount = 1`
- ArcadeDB: `pathCount = 0`

So the same family still reproduces on the later skeleton run, even when the final observable is just the later variable-length path count.

**Verification environment**
Verified directly against:

- Neo4j Docker container: `neo4j:latest`
- ArcadeDB Docker container: `arcadedata/arcadedb:latest`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.3.2`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.4.1-SNAPSHOT`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.4.2`
- Local differential-testing harness
- Direct ArcadeDB HTTP execution using the same request mode as Studio

**Stage3 provenance**
This report predates explicit file-level Stage3 provenance tracking.

The later skeleton differential seed was instantiated in:

- Stage3 run: `historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b`
- Stage4/45 run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_arcadedb_skeleton_20260430`
- Query ID: `ArcadeData__arcadedb__3128__q002`
- Case ID: `case_3f08615c7a71`
