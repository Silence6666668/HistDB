done
**Suggested title**
Repeated relationship variable in `WHERE` pattern may match rows instead of none

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
ArcadeDB may treat a repeated relationship variable inside a `WHERE` pattern as satisfiable and return rows that should not exist.

In the minimized repro below, the same relationship variable `r` is reused on both sides of:

```cypher
(p)-[r:KNOWS]->()<-[r:KNOWS]-()
```

Neo4j accepts the query but warns that the repeated relationship variable makes the pattern unsatisfiable, and it returns `0` rows.

ArcadeDB accepts the same query but returns `3` matching rows.

So ArcadeDB is not just accepting an edge-case pattern; it is evaluating it as true when the reference engine evaluates it as unsatisfiable.

**To Reproduce**

Setup:

```cypher
CREATE (:Person {name:'Alice'})-[:KNOWS]->(:Person {name:'Bob'})-[:KNOWS]->(:Person {name:'Charlie'});
```

Query:

```cypher
MATCH (p:Person)-[r:KNOWS]-()
WHERE (p)-[r:KNOWS]->()<-[r:KNOWS]-()
RETURN count(*) AS cnt;
```

**Expected behavior**
Observed Neo4j result:

```text
0
```

Neo4j also emits a warning that the repeated relationship variable `r` leads to no results.

**Actual behavior**
Observed ArcadeDB result:

```text
3
```

So rows survive a `WHERE` pattern that should be unsatisfiable.

**Control cases**

Without the repeated-variable `WHERE` pattern, both engines see the same four relationship matches:

```cypher
MATCH (p:Person)-[r:KNOWS]-()
RETURN count(*) AS cnt;
```

Observed result on Neo4j and ArcadeDB:

```text
4
```

If the repeated-variable pattern is moved into an `EXISTS` subquery, both engines again agree on `0`:

```cypher
MATCH (p:Person)-[r:KNOWS]-()
WHERE EXISTS {
  MATCH (p)-[r:KNOWS]->()<-[r:KNOWS]-()
}
RETURN count(*) AS cnt;
```

Observed result on Neo4j and ArcadeDB:

```text
0
```

So the issue is not simply that ArcadeDB cannot parse or accept the repeated variable. The bad behavior is specific to the top-level `WHERE` pattern evaluation.

**Stronger reproducer**
The same root issue becomes much larger in a query with earlier row fanout:

```cypher
MATCH (n:Person)--(m:Person)
WITH *
WHERE (n)--(m)
MATCH (p:Person)-[r:KNOWS]-()
WITH *
WHERE (p)-[r:KNOWS]->()<-[r:KNOWS]-()
RETURN count(*) AS cnt;
```

Observed result:

- Neo4j: `0`
- ArcadeDB: `12`

So the wrong truth value can amplify into large spurious row counts in bigger pipelines.

**Original differential seed**
This family surfaced while minimizing:

- `FalkorDB__FalkorDB__1333__q001`

That original generated query already showed the same direction of failure:

- Neo4j returned `0` rows
- ArcadeDB returned `12` rows

The minimized repro above shows the core issue is the repeated relationship variable inside the top-level `WHERE` pattern.

**Why this matters**
This is not a formatting difference or unsupported-syntax case. The query is accepted and evaluated, but ArcadeDB gives the opposite truth value from the reference engine and produces spurious rows.

That can silently corrupt later `count(*)`, `collect(...)`, `DISTINCT`, and write-after-filter pipelines.

**Stage3 provenance**
- Stage3 run: `historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b`
- Stage4/45 run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_arcadedb_skeleton_20260430`
- Differential seed query id: `FalkorDB__FalkorDB__1333__q001`
- Differential seed case id: `case_669bf0f1b0df`
