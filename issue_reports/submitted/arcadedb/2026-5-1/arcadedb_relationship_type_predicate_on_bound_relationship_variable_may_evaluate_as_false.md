done
**Suggested title**
`Relationship type predicate on a bound relationship variable may evaluate as false`

**ArcadeDB version**
Observed on Docker images:

- `arcadedata/arcadedb:latest`
- `arcadedata/arcadedb:26.3.2`
- `arcadedata/arcadedb:26.4.1-SNAPSHOT`
- `arcadedata/arcadedb:26.4.2`

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
ArcadeDB may evaluate a relationship type predicate such as `b:KNOWS` as false even after `b` has already been bound by a relationship pattern.

In the minimized repro below:

- the pattern `MATCH (a)-[b]->(c)` clearly binds two relationships
- both of those relationships are of type `KNOWS`

Neo4j therefore returns both rows.
ArcadeDB instead returns zero rows.

This does not look like a general relationship-type problem:

- the equivalent typed pattern `MATCH (a)-[b:KNOWS]->(c)` works
- the equivalent filter `WHERE type(b) = 'KNOWS'` also works

So the failure appears specific to the `relVar:TYPE` predicate form after the relationship variable is already bound.

**To Reproduce**

Setup:

```cypher
CREATE (n1:Person {name: 'Alice'}),
       (n2:Person {name: 'Bob'}),
       (n3:Person {name: 'Charlie'});

MATCH (n1:Person {name: 'Alice'}), (n2:Person {name: 'Bob'})
CREATE (n1)-[:KNOWS]->(n2);

MATCH (n2:Person {name: 'Bob'}), (n3:Person {name: 'Charlie'})
CREATE (n2)-[:KNOWS]->(n3);
```

Query:

```cypher
MATCH (a)-[b]->(c)
WHERE b:KNOWS
RETURN count(b) AS cnt;
```

**Expected behavior**
Neo4j returns:

```text
cnt = 2
```

ArcadeDB should return the same result.

**Actual behavior**
Observed ArcadeDB result on all tested versions:

```text
cnt = 0
```

So the bound relationship variable `b` is treated as if it were not of type `KNOWS`, even though both matched relationships are `KNOWS`.

**Control cases**

If the relationship type is expressed directly in the pattern, ArcadeDB behaves correctly:

```cypher
MATCH (a)-[b:KNOWS]->(c)
RETURN count(b) AS cnt;
```

Observed result on Neo4j and all tested ArcadeDB versions:

```text
cnt = 2
```

If the same check is expressed using `type(b)`, ArcadeDB also behaves correctly:

```cypher
MATCH (a)-[b]->(c)
WHERE type(b) = 'KNOWS'
RETURN count(b) AS cnt;
```

Observed result on Neo4j and all tested ArcadeDB versions:

```text
cnt = 2
```

This makes the boundary fairly sharp:

- `WHERE b:KNOWS` fails
- `MATCH ...-[b:KNOWS]->...` works
- `WHERE type(b) = 'KNOWS'` works

So this looks like a wrong-result bug in ArcadeDB's handling of relationship type predicates over already bound relationship variables, not a general type-resolution failure.

**Differential seed**
This family surfaced in the skeleton differential run from:

- `RedisGraph__RedisGraph__2270__q002`

Observed skeleton-run differential case:

```cypher
MATCH (a)-[b]->(c)
WHERE b:KNOWS
RETURN b;
```

Observed results there:

- Neo4j: `2` rows
- ArcadeDB: `0` rows

The minimized count-based repro above shows the core issue more directly.

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
- Stage3 run: `historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b`
- Stage3 file: `data/batch_runs/historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b/stage3_main/instantiated_cases.jsonl`
- Stage45 run: `data/batch_runs/historical_bug_queries_parallel_llm_v1_stage45_neo4j_arcadedb_skeleton_20260430/comparison_results.jsonl`
- Query ID: `RedisGraph__RedisGraph__2270__q002`
- Case ID: `case_323b9d10e584`
- Prepared source ID: `RedisGraph/RedisGraph#2270`
