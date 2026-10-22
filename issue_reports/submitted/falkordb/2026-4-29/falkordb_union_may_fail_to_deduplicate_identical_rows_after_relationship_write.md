**Suggested title**
`UNION may fail to deduplicate identical rows after a relationship write in an earlier branch`

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
- Client used for direct reproduction: direct `GRAPH.QUERY`
- Differential comparison target: Neo4j Community `2026.02.2`

**Describe the bug**
FalkorDB may fail to apply `UNION` deduplication correctly when:

- the first branch writes a relationship
- both branches return the same scalar row derived from that relationship

In the minimized repro below, both branches produce the same row:

```text
Alice, TechCorp, 2020
```

Neo4j correctly returns one row because `UNION` should deduplicate identical results.
FalkorDB returns the same row twice.

This suggests FalkorDB is not deduplicating `UNION` results correctly when an earlier branch writes a relationship and a later branch reads that same relationship in the same query.

**To Reproduce**
1. Start the FalkorDB Docker container.
2. Run:

```cypher
MERGE (p:Person {name: 'Alice'})
MERGE (c:Company {name: 'TechCorp'})
MERGE (p)-[r:WORKS_AT]->(c)
SET r.since = 2020
RETURN p.name AS personName, c.name AS companyName, r.since AS since
UNION
MATCH (p:Person {name: 'Alice'})
OPTIONAL MATCH (p)-[r:WORKS_AT]->(c)
RETURN p.name AS personName, c.name AS companyName, r.since AS since;
```

**Expected behavior**
Neo4j returns one deduplicated row:

```text
Alice, TechCorp, 2020
```

FalkorDB should return the same single row.

**Actual behavior**
FalkorDB returns the same row twice:

```text
Alice, TechCorp, 2020
Alice, TechCorp, 2020
```

**Control query 1**
If `UNION ALL` is used instead, both engines return two rows:

```cypher
MERGE (p:Person {name: 'Alice'})
MERGE (c:Company {name: 'TechCorp'})
MERGE (p)-[r:WORKS_AT]->(c)
SET r.since = 2020
RETURN p.name AS personName, c.name AS companyName, r.since AS since
UNION ALL
MATCH (p:Person {name: 'Alice'})
OPTIONAL MATCH (p)-[r:WORKS_AT]->(c)
RETURN p.name AS personName, c.name AS companyName, r.since AS since;
```

Observed result on both Neo4j and FalkorDB:

```text
Alice, TechCorp, 2020
Alice, TechCorp, 2020
```

So the issue is not that the second branch should be suppressed entirely; it is specifically about `UNION` deduplication.

**Control query 2**
If the first branch writes only a node and both branches return the same scalar node value, `UNION` deduplication works on both engines:

```cypher
MERGE (p:Person {name: 'Alice'})
RETURN p.name AS personName
UNION
MATCH (p:Person {name: 'Alice'})
RETURN p.name AS personName;
```

Observed result on both Neo4j and FalkorDB:

```text
Alice
```

So `UNION` itself is not generally broken after an earlier write.

**Control query 3**
If the relationship already exists before the query and both branches are read-only, `UNION` deduplication also works on both engines:

```cypher
CREATE (:Person {name: 'Alice'})-[:WORKS_AT {since: 2020}]->(:Company {name: 'TechCorp'});

MATCH (p:Person {name: 'Alice'})-[r:WORKS_AT]->(c:Company)
RETURN p.name AS personName, c.name AS companyName, r.since AS since
UNION
MATCH (p:Person {name: 'Alice'})-[r:WORKS_AT]->(c:Company)
RETURN p.name AS personName, c.name AS companyName, r.since AS since;
```

Observed result on both Neo4j and FalkorDB:

```text
Alice, TechCorp, 2020
```

This makes the boundary clearer: the bad deduplication appears tied to a relationship write in an earlier branch of the same query, not to `UNION` or relationship reads in general.

**Differential seed**
This family was first noticed in:

- `ArcadeData__arcadedb__3476__q004`

**Verification Environment**
Plain FalkorDB Docker image is enough.

Preferred verification target:
- Docker image
- x86_64
- `falkordb/falkordb`

I can verify a fix using:
- plain FalkorDB Docker image on Windows host
- direct `GRAPH.QUERY`
