done
**Suggested title**
`Anonymous middle node in a linear relationship chain may cause the pattern to match no rows`

**ArcadeDB version**
Observed on Docker images:

- `arcadedata/arcadedb:26.4.1-SNAPSHOT`
- `arcadedata/arcadedb:26.4.2`

Not reproduced on:

- `arcadedata/arcadedb:26.3.2`

**Discovered on**
- `2026-05-01`

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
On ArcadeDB `26.4.1-SNAPSHOT` and `26.4.2`, a simple linear chain pattern may incorrectly match no rows when the middle node is written as an anonymous node pattern.

In the minimized repro below, the graph contains:

- `(:Person {name:'Alice'})-[:KNOWS]->(:City {name:'New York'})`
- `(:City {name:'New York'})-[:LOCATED_IN]->(:Country {name:'USA'})`

Neo4j matches the obvious two-hop chain.
ArcadeDB returns `0 rows` if the middle `City` node is anonymous:

```cypher
(a:Person)-[:KNOWS]->(:City)-[:LOCATED_IN]->(b:Country)
```

But the equivalent query with the same middle node explicitly named works correctly on ArcadeDB:

```cypher
(a:Person)-[:KNOWS]->(c:City)-[:LOCATED_IN]->(b:Country)
```

So this does not look like a general multi-hop pattern problem.
The failure appears to be tied specifically to the anonymous middle node form.

**To Reproduce**

Setup:

```cypher
CREATE (:Person {id: 1, name:'Alice', age:30}),
       (:City {name:'New York'}),
       (:Country {name:'USA'});

MATCH (a:Person {id: 1}),
      (c:City {name:'New York'}),
      (u:Country {name:'USA'})
CREATE (a)-[:KNOWS]->(c),
       (c)-[:LOCATED_IN]->(u);
```

Query:

```cypher
MATCH (a:Person)-[:KNOWS]->(:City)-[:LOCATED_IN]->(b:Country)
RETURN a.name AS person_name, b.name AS country_name;
```

**Expected behavior**
Observed Neo4j result:

```text
Alice, USA
```

ArcadeDB should return the same row.

**Actual behavior**
Observed ArcadeDB results for the main repro:

```text
26.4.1-SNAPSHOT: 0 rows
26.4.2:          0 rows
26.3.2:          Alice, USA
```

So the anonymous-middle-node form silently fails to match on the affected `26.4.x` line.

**Control cases**

If the same middle node is explicitly named, ArcadeDB behaves correctly:

```cypher
MATCH (a:Person)-[:KNOWS]->(c:City)-[:LOCATED_IN]->(b:Country)
RETURN a.name AS person_name, b.name AS country_name;
```

Observed result on Neo4j and all tested ArcadeDB versions:

```text
Alice, USA
```

So the graph shape itself is valid.

The same boundary also appears with aggregation:

```cypher
MATCH (a:Person)-[:KNOWS]->(:City)-[:LOCATED_IN]->(b:Country)
RETURN b.name AS country_name, collect(a.name) AS people;
```

Observed results:

- Neo4j: `USA, ['Alice']`
- ArcadeDB `26.4.1-SNAPSHOT`: `0 rows`
- ArcadeDB `26.4.2`: `0 rows`

But the named-middle-node version aggregates correctly on ArcadeDB:

```cypher
MATCH (a:Person)-[:KNOWS]->(c:City)-[:LOCATED_IN]->(b:Country)
RETURN b.name AS country_name, collect(a.name) AS people;
```

Observed result on Neo4j and all tested ArcadeDB versions:

```text
USA, ['Alice']
```

This makes the boundary fairly sharp:

- single-hop patterns work
- the same two-hop chain works when the middle node is named
- the failure starts when the middle node is anonymous

**Why this matters**
The anonymous node form is a standard equivalent spelling of the same path shape.
Silently returning `0 rows` changes query semantics and can corrupt downstream:

- `count(*)`
- `collect(...)`
- `OPTIONAL MATCH`
- write-after-match pipelines

even though the underlying graph does satisfy the pattern.

**Differential seed**
This family surfaced while minimizing:

- `FalkorDB__FalkorDB__1437__q002`

The original generated query was:

```cypher
MATCH (a:Person)-[:KNOWS]->(:City)-[:LOCATED_IN]->(b:Country)
RETURN b.name AS country_name, collect(a.name) AS people;
```

Observed differential result:

- Neo4j: `USA, ['Alice']`
- ArcadeDB: `0 rows`

Direct reduction shows the core issue is the anonymous middle node inside the linear chain, not the aggregation.

**Verification environment**
Verified directly against:

- Neo4j Docker container: `neo4j:latest`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.3.2`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.4.1-SNAPSHOT`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.4.2`
- Local differential-testing harness
- Direct ArcadeDB HTTP execution using the same request mode as Studio

**Stage3 provenance**
- Stage3 run: `historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b`
- Query ID: `FalkorDB__FalkorDB__1437__q002`
- Case ID: `case_6434ef2623e9`
- Stage3 artifact: `data/batch_runs/historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b/stage3_main/instantiated_cases.jsonl`
- Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_arcadedb_skeleton_20260430`
- Differential bucket: `all_passed_different_result`
