done
**Suggested title**
`Node label-union pattern may match no rows even when either label exists`

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
ArcadeDB accepts Neo4j-style node label-union syntax such as:

```cypher
(n:Person|Company)
```

but may evaluate it as matching no rows, even when the graph clearly contains `Person` or `Company` nodes that should satisfy the pattern.

This is stronger than a parser-support gap:

- the query is accepted without a syntax error
- the equivalent logic expressed as `WHERE n:Person OR n:Company` works correctly
- the same failure appears across `26.3.2`, `26.4.1-SNAPSHOT`, `26.4.2`, and the moving `latest` tag

So the current behavior is a silent wrong-result outcome rather than an explicit "unsupported syntax" rejection.

**To Reproduce**

Setup:

```cypher
CREATE (:Person {name:'Alice'}),
       (:Company {name:'TechCorp'});
```

Query:

```cypher
MATCH (n:Person|Company)
RETURN n.name AS name
ORDER BY name;
```

**Expected behavior**
Both nodes should match.

Observed Neo4j result:

```text
Alice
TechCorp
```

**Actual behavior**
Observed ArcadeDB result on all tested versions:

```text
0 rows
```

So the node label-union pattern behaves as if neither label matches.

**Control cases**

A single-label node pattern works correctly on ArcadeDB:

```cypher
MATCH (n:Person)
RETURN n.name AS name
ORDER BY name;
```

Observed result on Neo4j and all tested ArcadeDB versions:

```text
Alice
```

The equivalent logic expressed with label predicates also works correctly on ArcadeDB:

```cypher
MATCH (n)
WHERE n:Person OR n:Company
RETURN n.name AS name
ORDER BY name;
```

Observed result on Neo4j and all tested ArcadeDB versions:

```text
Alice
TechCorp
```

The same failure also appears with a property-constrained label-union node pattern:

```cypher
CREATE (:Person {name:'Alice'});

MATCH (n:Person|Company {name:'Alice'})
RETURN count(n) AS c;
```

Observed Neo4j result:

```text
1
```

Observed ArcadeDB result on all tested versions:

```text
0
```

This suggests the issue is specifically in how ArcadeDB evaluates the `:A|B` node-pattern form, not in label checks in general.

**Stronger fallout**
This also explains a larger differential seed that initially looked like a `CALL` / `DETACH DELETE` issue:

- `FalkorDB__FalkorDB__1012__q002`

That query contained:

```cypher
OPTIONAL MATCH (n:Person|Company {name: personName})
DETACH DELETE n
RETURN count(n) AS deletedCount
```

In direct minimization:

- Neo4j returns `deletedCount = 1`
- ArcadeDB returns `deletedCount = 0`

But the same minimized shape with a single-label match:

```cypher
OPTIONAL MATCH (n:Person {name: p.name})
DETACH DELETE n
RETURN count(n) AS deletedCount
```

works correctly on ArcadeDB and returns `1`.

So the larger row-loss behavior collapses into this simpler root cause: the `:Person|Company` node pattern silently fails to match.

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
The original family was first surfaced while minimizing:

- Differential seed: `FalkorDB__FalkorDB__1012__q002`

The same family was independently resurfaced in the current skeleton differential:

- Stage3 run: `historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b`
- Query ID: `FalkorDB__FalkorDB__247__q019`
- Case ID: `case_aa53049aaad7`
- Stage3 artifact: `data/batch_runs/historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b/stage3_main/instantiated_cases.jsonl`
- Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_arcadedb_skeleton_20260430`
- Differential bucket: `all_passed_different_result`
