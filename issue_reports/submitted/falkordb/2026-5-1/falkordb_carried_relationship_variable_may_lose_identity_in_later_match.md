done
**Suggested title**
`Relationship variable carried through WITH may lose identity in a later MATCH`

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
FalkorDB may lose the identity of a relationship variable after it is carried through `WITH` and then reused in a later `MATCH`.

In the minimized repro below, the first clause binds exactly one `[:KNOWS]` relationship into `r`.

Neo4j keeps that identity, so the later:

```cypher
MATCH (x:Person)-[r]->(y)
```

still returns only the original `Alice -> Bob` relationship.

FalkorDB instead also matches a different `[:FRIEND]` relationship and returns an extra row `Alice -> Charlie`.

This suggests the later `MATCH` is no longer respecting the original identity of `r`.

**To Reproduce**
1. Start the FalkorDB Docker container.
2. Run:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice'}), (:Person {name:'Bob'}), (:Person {name:'Charlie'});
MATCH (a:Person {name:'Alice'}), (b:Person {name:'Bob'}), (c:Person {name:'Charlie'})
CREATE (a)-[:KNOWS]->(b), (a)-[:FRIEND]->(c);

MATCH ()-[r:KNOWS]->()
WITH r
MATCH (x:Person)-[r]->(y)
RETURN x.name AS x, y.name AS y
ORDER BY x, y;
```

**Expected behavior**
Neo4j returns:

```text
Alice, Bob
```

FalkorDB should return the same single row.

**Actual behavior**
FalkorDB returns:

```text
Alice, Bob
Alice, Charlie
```

So the later `MATCH` can reuse `r` as if it were some relationship other than the one that was already bound.

**Control query 1**
If there is only one relationship in the graph, both engines behave normally:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice'}), (:Person {name:'Bob'});
MATCH (a:Person {name:'Alice'}), (b:Person {name:'Bob'})
CREATE (a)-[:KNOWS]->(b);

MATCH ()-[r:KNOWS]->()
WITH r
MATCH (x:Person)-[r]->(y)
RETURN x.name AS x, y.name AS y
ORDER BY x, y;
```

Observed result on both Neo4j and FalkorDB:

```text
Alice, Bob
```

So the issue is not that FalkorDB always duplicates rows after `WITH r`. The bad behavior appears once there is another relationship available to match instead.

**Control query 2**
Carrying the relationship variable through `WITH` and returning it directly works on both engines:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice'}), (:Person {name:'Bob'}), (:Person {name:'Charlie'});
MATCH (a:Person {name:'Alice'}), (b:Person {name:'Bob'}), (c:Person {name:'Charlie'})
CREATE (a)-[:KNOWS]->(b), (a)-[:FRIEND]->(c), (b)-[:KNOWS]->(c);

MATCH ()-[r:KNOWS|FRIEND]->()
WITH r
RETURN type(r) AS t
ORDER BY t;
```

Observed result on both Neo4j and FalkorDB:

```text
FRIEND
KNOWS
KNOWS
```

So the issue is not that `WITH r` immediately corrupts the value. The failure starts when the carried relationship variable is reused in a later `MATCH`.

**Control query 3**
The same problem becomes even clearer if the first match binds two `[:KNOWS]` rows while the graph also contains one `[:FRIEND]` edge:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice'}), (:Person {name:'Bob'}), (:Person {name:'Charlie'});
MATCH (a:Person {name:'Alice'}), (b:Person {name:'Bob'}), (c:Person {name:'Charlie'})
CREATE (a)-[:KNOWS]->(b), (a)-[:FRIEND]->(c), (b)-[:KNOWS]->(c);

MATCH ()-[r:KNOWS]->()
WITH r
MATCH (x:Person)-[r]->(y)
RETURN x.name AS x, y.name AS y
ORDER BY x, y;
```

Observed results:

- Neo4j:

```text
Alice, Bob
Bob, Charlie
```

- FalkorDB:

```text
Alice, Bob
Alice, Bob
Alice, Charlie
Alice, Charlie
Bob, Charlie
Bob, Charlie
```

This makes the boundary much clearer: once the later `MATCH` reuses a relationship variable carried through `WITH`, FalkorDB can match unrelated edges instead of the originally bound relationship identity.

**Additional supporting reproducer**
The same family also appears as a pure row-duplication bug when the later `MATCH` is further filtered:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a {name: 'Alice', tags: ['dev', 'ops']}),
       (b {name: 'Bob', tags: ['dev']}),
       (c {name: 'Charlie', tags: ['ops']}),
       (a)-[:KNOWS]->(b),
       (a)-[:KNOWS]->(c);

MATCH ()-[r]->()
WITH r
MATCH (x)-[r]->(y)
WHERE x.name CONTAINS 'A' AND y.name CONTAINS 'B'
RETURN x.name AS x, y.name AS y
ORDER BY x, y;
```

Observed results:

- Neo4j:

```text
Alice, Bob
```

- FalkorDB:

```text
Alice, Bob
Alice, Bob
```

So even when the later `MATCH` ultimately lands on the same logical edge, FalkorDB can still duplicate the row after the carried relationship variable loses its original identity constraints.

**Additional supporting reproducer 2**
The same family also appears when a carried relationship variable is reused in a later `MATCH` without any additional node predicates at all:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice'})-[:KNOWS]->(:Person {name:'Bob'}),
       (:Person {name:'Carol'})-[:KNOWS]->(:Person {name:'Dave'});

MATCH ()-[r:KNOWS]->()
WITH r AS rel
MATCH ()-[rel]->()
RETURN rel AS result;
```

Observed results:

- Neo4j:

```text
2 rows
```

- FalkorDB `v4.18.2`:

```text
4 rows
```

The two physical `[:KNOWS]` edges are each returned twice.

**Control for supporting reproducer 2**
If the second `MATCH` is removed and the carried variable is returned directly, both engines agree:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice'})-[:KNOWS]->(:Person {name:'Bob'}),
       (:Person {name:'Carol'})-[:KNOWS]->(:Person {name:'Dave'});

MATCH ()-[rel:KNOWS]->()
RETURN rel AS result;
```

Observed result on both Neo4j and FalkorDB:

```text
2 rows
```

So the issue is again tied to reusing the carried relationship variable in a later `MATCH`, not to the base graph shape or to returning relationships directly.

**Differential seed**
This family surfaced while minimizing:

- `FalkorDB__FalkorDB__127__q002`

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
- Query ID: `FalkorDB__FalkorDB__127__q002`
- Case ID: `case_136c24af6f23`
- Supporting seed: `RedisGraph__RedisGraph__945__q002` / `case_c6c03adae166`
- Supporting seed: `RedisGraph__RedisGraph__526__q001` / `case_b1105869479b`
