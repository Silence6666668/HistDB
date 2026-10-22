## FalkorDB version
Observed on:

- FalkorDB `4.16.8`
- FalkorDB `4.18.2`

Docker images used locally:
- historical minimized verification: `falkordb/falkordb-server:latest` with local container tag `4.16.8`
- current re-verification: `falkordb/falkordb:v4.18.2`

## Environment
- Docker on Windows host
- FalkorDB queried through the existing RESP-based executor
- Differential comparison target used during minimization: Memgraph `3.8.1`

## Describe the bug
FalkorDB may fail to create a relationship when a `MERGE` node pattern uses a string alias returned from a `CALL { ... }` subquery.

In the minimized repro below, the subquery returns `friend_name = 'Harry'`, and the main query then uses that alias in:

```cypher
MERGE (p)-[:HAS_FRIEND]->(f:Person { name: friend_name })
```

The query succeeds on Memgraph, but FalkorDB fails with:

```text
Failed to create relationship; endpoint was not found.
```

The same minimized family was re-verified on FalkorDB `4.18.2`, where it still fails with the same error.

## To Reproduce

### Setup
```cypher
CREATE (c:Country {name: 'France'});
CREATE (p:Person {name: 'John'});
CREATE (f:Person {name: 'Harry'});
MATCH (p:Person {name: 'John'}), (c:Country {name: 'France'}) CREATE (p)-[:WORKING_IN]->(c);
MATCH (p:Person {name: 'John'}), (f:Person {name: 'Harry'}) CREATE (p)-[:FRIENDS_WITH]->(f);
```

### Query
```cypher
MATCH (p:Person)-[:WORKING_IN]->(:Country)
CALL {
  WITH p
  MATCH (p)-[:FRIENDS_WITH]->(f:Person)
  RETURN f.name AS friend_name
  LIMIT 1
}
MERGE (p)-[:HAS_FRIEND]->(f:Person { name: friend_name })
RETURN p.name AS person_name, friend_name
```

## Expected behavior
The query should succeed and return:

```text
person_name = John
friend_name = Harry
```

## Actual behavior
FalkorDB fails with:

```text
Failed to create relationship; endpoint was not found.
```

## Control cases

### 1. Split node creation/matching from relationship creation

This nearby rewrite succeeds:

```cypher
MATCH (p:Person)-[:WORKING_IN]->(:Country)
CALL {
  WITH p
  MATCH (p)-[:FRIENDS_WITH]->(f:Person)
  RETURN f.name AS friend_name
  LIMIT 1
}
MERGE (f2:Person { name: friend_name })
MERGE (p)-[:HAS_FRIEND]->(f2)
RETURN p.name AS person_name, friend_name
```

### 2. Return the node itself from the subquery

This also succeeds:

```cypher
MATCH (p:Person)-[:WORKING_IN]->(:Country)
CALL {
  WITH p
  MATCH (p)-[:FRIENDS_WITH]->(f:Person)
  RETURN f
  LIMIT 1
}
MERGE (p)-[:HAS_FRIEND]->(f)
RETURN p.name AS person_name, f.name AS friend_name
```

These controls suggest the failure is specifically tied to using a subquery-returned string alias inside a node pattern that is part of a relationship `MERGE`.

## Additional context
- The issue still reproduces after removing the original `OPTIONAL MATCH` / `COUNT` wrapper from the larger query.
- The setup can be reduced to one country node, two person nodes, and two relationships.
- This does not look like a general `CALL` problem or a general `MERGE` problem; it appears to be the combination of:
  - subquery string alias output
  - node pattern lookup by property
  - relationship creation in the same `MERGE`

## Current-version supporting seed
The same family also appeared again in the `neo4j vs FalkorDB 4.18.2` skeleton differential run.

Supporting Stage3 case:

```cypher
CREATE (p:Person {name: 'Alice'})-[:KNOWS]->(:City {name: 'NYC'})
CREATE (p)-[:LIKES]->(f:Friend {name: 'Bob', age: 30})

MATCH (p:Person)-[:KNOWS]->(:City)
CALL {
  WITH p
  MATCH (p)-[:LIKES]->(f:Friend)
  RETURN f.name AS friendName
  LIMIT 1
}
MERGE (p)-[:HAS]->(f:Friend {name: friendName})
RETURN p.name AS personName, friendName
```

Observed results:

- Neo4j: `Alice, Bob`
- FalkorDB `4.18.2`: `Failed to create relationship; endpoint was not found.`

So this is not just an older-line issue. The same underlying behavior is still present on FalkorDB `4.18.2`.

## Verification environment
Verified using:
- plain FalkorDB Docker image
- RESP execution path from the local differential-testing harness

Supporting artifact:
- [tmp_falkor_q1889_min_v1](D:/sci/works/get%20bugs/graph-bug-fuzz-mvp/data/stage45_runs/tmp_falkor_q1889_min_v1/summary.json)

**Stage3 provenance**
- This report predates explicit file-level Stage3 provenance tracking for its original minimization.
- Current-version supporting seed was observed in:
  - Stage3 run: `historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b`
  - Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_falkordb_4_18_2_skeleton_20260501`
  - Query ID: `FalkorDB__FalkorDB__1849__q002`
  - Case ID: `case_ff951857a3b5`
