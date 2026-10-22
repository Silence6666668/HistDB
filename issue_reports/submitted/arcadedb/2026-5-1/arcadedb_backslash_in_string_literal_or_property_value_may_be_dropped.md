done
**Suggested title**
`Backslash in a string literal or property value may be dropped`

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
ArcadeDB may drop a backslash character from string literals and from returned property values.

In the minimized repro below, Neo4j preserves the string `Alice\Bob`.
ArcadeDB returns `AliceBob`.

This is not just a presentation issue.
ArcadeDB also treats a stored property created from `Alice\Bob` as equal to `AliceBob`, which suggests the backslash is being discarded during parsing or value handling.

**To Reproduce**

Query:

```cypher
RETURN 'Alice\Bob' AS name;
```

**Expected behavior**
Observed Neo4j result:

```text
Alice\Bob
```

**Actual behavior**
Observed ArcadeDB result:

```text
AliceBob
```

**Control cases**

The same issue appears when the value is stored in a node property and then read back:

Setup:

```cypher
CREATE (n:Person {name: 'Alice\Bob'});
```

Query:

```cypher
MATCH (n:Person)
RETURN n.name AS name;
```

Observed results:

- Neo4j: `Alice\Bob`
- ArcadeDB: `AliceBob`

This shows the problem is not limited to direct literal projection.

ArcadeDB also matches the stored node against the backslash-free form:

```cypher
MATCH (n:Person)
WHERE n.name = 'AliceBob'
RETURN count(*) AS c;
```

Observed results:

- Neo4j: `0`
- ArcadeDB: `1`

While both engines still match the backslash form:

```cypher
MATCH (n:Person)
WHERE n.name = 'Alice\Bob'
RETURN count(*) AS c;
```

Observed results:

- Neo4j: `1`
- ArcadeDB: `1`

So ArcadeDB is effectively normalizing away the backslash instead of preserving the exact string value.

Returning a list that contains the same literal also shows the loss:

```cypher
RETURN ['Alice\Bob'] AS xs;
```

Observed results:

- Neo4j: `['Alice\Bob']`
- ArcadeDB: `['AliceBob']`

**Why this matters**
This changes the actual string value, not just its formatting.
Client code that depends on exact identifiers, escaped names, or path-like strings can observe silent corruption.

**Differential seed**
This family surfaced while minimizing:

- `ArcadeData__arcadedb__3479__q001`

**Stage3 provenance**
- Stage3 run: `historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b`
- Stage3 file: `data/batch_runs/historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b/stage3_main/instantiated_cases.jsonl`
- Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_arcadedb_skeleton_20260430`
- Differential bucket: `all_passed_different_result`
- Example case id: `case_e96dd0843f16`
