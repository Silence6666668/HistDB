done
**Suggested title**
`MATCH on a null carried variable may keep the row instead of filtering it out`

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
When a variable becomes `null` through `OPTIONAL MATCH` and is then carried through `WITH`, a later `MATCH` on that same variable may incorrectly keep the row alive on ArcadeDB.

In Cypher semantics, if `c` is already bound to `null`, then:

```cypher
MATCH (c:City {population: 8000000})
```

should not rebind `c` or preserve the row. Neo4j returns no rows.
ArcadeDB keeps the row and still returns `c = null`.

**To Reproduce**

Setup:

```cypher
CREATE (:Person {name:'Alice', city:'New York'}),
       (:City {name:'New York', population:8000000});
```

Query:

```cypher
MATCH (p:Person {city:'New York'})
OPTIONAL MATCH (p)-[:LIVES_IN]->(c:City)
WITH p, c
MATCH (c:City {population:8000000})
RETURN p.name AS p, c.name AS c;
```

**Expected behavior**
The `OPTIONAL MATCH` finds no `LIVES_IN` relationship, so `c` is `null`.
The later `MATCH` on `c` should therefore eliminate the row.

Observed Neo4j result:

```text
0 rows
```

**Actual behavior**
ArcadeDB returns:

```text
Alice, null
```

So the row survives even though the carried variable `c` is still null.

**Control cases**

The `OPTIONAL MATCH` by itself behaves normally:

```cypher
MATCH (p:Person {city:'New York'})
OPTIONAL MATCH (p)-[:LIVES_IN]->(c:City)
WITH p, c
RETURN p.name AS p, c.name AS c;
```

Observed result on both Neo4j and ArcadeDB:

```text
Alice, null
```

The incorrect survival still happens if the optional relationship variable is carried too:

```cypher
MATCH (p:Person {city:'New York'})
OPTIONAL MATCH (p)-[r:LIVES_IN]->(c:City)
WITH p, c, r
MATCH (c:City {population:8000000})
RETURN p.name AS p, c.name AS c;
```

Neo4j returns no rows.
ArcadeDB still returns:

```text
Alice, null
```

Even adding a no-op style write on the unmatched relationship alias does not change the behavior:

```cypher
MATCH (p:Person {city:'New York'})
OPTIONAL MATCH (p)-[r:LIVES_IN]->(c:City)
SET r.duration = 5
WITH p, c, r
MATCH (c:City {population:8000000})
RETURN p.name AS p, c.name AS c;
```

Neo4j returns no rows.
ArcadeDB still returns:

```text
Alice, null
```

This suggests the issue is specifically that a later `MATCH` does not correctly eliminate rows when the referenced carried variable is already null.

**Differential seed**
This family was first noticed in the latest Neo4j-vs-ArcadeDB differential run from:

- `FalkorDB__FalkorDB__351__q002`

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
The underlying differential-seed source query was instantiated in:

- Stage3 run: `historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b`
- Query ID: `FalkorDB__FalkorDB__351__q002`
- Case ID: `case_7743c5e39883`
- Stage3 artifact: `data/batch_runs/historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b/stage3_main/instantiated_cases.jsonl`
