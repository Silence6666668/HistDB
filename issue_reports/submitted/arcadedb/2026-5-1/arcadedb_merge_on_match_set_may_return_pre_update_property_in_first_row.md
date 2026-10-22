done
**Suggested title**
`MERGE ... ON MATCH SET may return the pre-update property value in the first row of a multi-row query`

**ArcadeDB version**
Observed on Docker images:

- `arcadedata/arcadedb:26.4.1-SNAPSHOT`
- `arcadedata/arcadedb:26.4.2`

Not reproduced on:

- `arcadedata/arcadedb:26.3.2`
- the current local moving `arcadedata/arcadedb:latest`

So this currently looks like a narrow regression in the `26.4.x` line rather than a stable long-lived behavior.

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
In a multi-row query, `MERGE ... ON MATCH SET` may persist the property update correctly, but still return the old property value in the first output row.

The update itself is not lost. After the query finishes, the stored value is correct.
The problem seems to be the value materialized into the first returned row immediately after the `ON MATCH SET`.

Neo4j returns the updated value in every row.

**To Reproduce**

Setup:

```cypher
CREATE (:Traveler {name:'Alice'});
CREATE (:Town {name:'London', population: 8900000});
```

Query:

```cypher
MERGE (p:Traveler {name:'Alice'})
WITH p
UNWIND [1, 2, 3] AS i
MERGE (p)-[:VISITS]->(c:Town {name:'London'})
ON MATCH SET c.population = 9000000
RETURN i, c.population AS population
ORDER BY i;
```

**Expected behavior**
All rows should observe the updated value:

```text
1, 9000000
2, 9000000
3, 9000000
```

Observed Neo4j result:

```text
1, 9000000
2, 9000000
3, 9000000
```

Observed ArcadeDB `26.3.2` result:

```text
1, 9000000
2, 9000000
3, 9000000
```

**Actual behavior**
ArcadeDB `26.4.1-SNAPSHOT` and `26.4.2` return:

```text
1, 8900000
2, 9000000
3, 9000000
```

So only the first returned row exposes the pre-update value, even though the same query has already executed `ON MATCH SET c.population = 9000000`.

**Control cases**

If the query produces only one row, ArcadeDB behaves correctly:

```cypher
MERGE (p:Traveler {name:'Alice'})
WITH p
UNWIND [1] AS i
MERGE (p)-[:VISITS]->(c:Town {name:'London'})
ON MATCH SET c.population = 9000000
RETURN i, c.population AS population;
```

Observed result on Neo4j and all tested ArcadeDB versions:

```text
1, 9000000
```

If `MERGE ... ON MATCH SET` is used without the multi-row `UNWIND`, ArcadeDB also behaves correctly:

```cypher
MATCH (p:Traveler {name:'Alice'})
MERGE (p)-[:VISITS]->(c:Town {name:'London'})
ON MATCH SET c.population = 9000000
RETURN c.population AS population;
```

Observed result on Neo4j and all tested ArcadeDB versions:

```text
9000000
```

The persisted value is also correct after the multi-row query finishes:

```cypher
MATCH (:Traveler {name:'Alice'})-[:VISITS]->(c:Town {name:'London'})
RETURN c.population AS final_population;
```

Observed result on Neo4j and all tested ArcadeDB versions:

```text
9000000
```

This makes the boundary clearer:

- the property update itself is applied
- single-row `ON MATCH SET` behaves normally
- the regression appears in the first returned row of a multi-row pipeline

**Original differential seed**
This family was first noticed in:

- `ArcadeData__arcadedb__3326__q001`

The original generated query used `Person` / `City` labels and a slightly larger row shape, but the minimized reproducer above keeps the same essential failure mode.

**Verification environment**
Verified directly against:

- Neo4j Docker container: `neo4j:latest`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.3.2`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.4.1-SNAPSHOT`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.4.2`
- ArcadeDB Docker container: current moving `arcadedata/arcadedb:latest`
- Local differential-testing harness
- Direct ArcadeDB HTTP execution using the same request mode as Studio

**Stage3 provenance**
This report predates explicit file-level Stage3 provenance tracking.
The underlying differential-seed source query was instantiated in:

- Stage3 run: `historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b`
- Query ID: `ArcadeData__arcadedb__3326__q001`
- Case ID: `case_9ac4469e476f`
- Stage3 artifact: `data/batch_runs/historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b/stage3_main/instantiated_cases.jsonl`
