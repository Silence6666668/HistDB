**Suggested title**
`Node label disjunction patterns may match no rows`

**ArcadeDB version**
Observed on Docker images:

- `arcadedata/arcadedb:26.4.2`

**Discovered on**
- `2026-05-12`

**Environment**
- Host OS: Windows
- Architecture: x86_64
- Deployment: Docker
- ArcadeDB endpoint: HTTP `/api/v1/command/arcade`
- Request mode matches ArcadeDB Studio:
  - `language: opencypher`
  - `serializer: studio`
- Differential comparison target: Neo4j Community `2026.04.0`

**Describe the bug**
ArcadeDB may match no rows for a node pattern that uses a label disjunction such as `(n:A|B)`, even when nodes with both labels clearly exist.

In the minimized repro below, the graph contains one `A` node and one `B` node.
Neo4j returns both rows.
ArcadeDB returns no rows.

This does not look like a general label-matching problem, because the same nodes are matched correctly when each label is queried separately.

**To Reproduce**

Setup:

```cypher
CREATE (:A {id: 1}),
       (:B {id: 2}),
       (:C {id: 3});
```

Query:

```cypher
MATCH (n:A|B)
RETURN n.id AS id
ORDER BY id;
```

**Expected behavior**
Observed Neo4j result:

```text
1
2
```

ArcadeDB should return the same two rows.

**Actual behavior**
Observed ArcadeDB `26.4.2` result:

```text
0 rows
```

So the label-disjunction pattern behaves as if no matching nodes exist.

**Control cases**

Single-label matches work correctly on both engines:

```cypher
MATCH (n:A) RETURN count(*) AS cnt;
MATCH (n:B) RETURN count(*) AS cnt;
MATCH (n:C) RETURN count(*) AS cnt;
```

Observed result on Neo4j and ArcadeDB `26.4.2`:

```text
cnt = 1
```

So the data and ordinary label lookup are fine.

The failure is specific to disjunction. Two-label and three-label forms both fail on ArcadeDB:

```cypher
MATCH (n:A|B) RETURN count(*) AS cnt;
MATCH (n:A|C) RETURN count(*) AS cnt;
MATCH (n:B|C) RETURN count(*) AS cnt;
MATCH (n:A|B|C) RETURN count(*) AS cnt;
```

Observed results:

- Neo4j:
  - `A|B`: `2`
  - `A|C`: `2`
  - `B|C`: `2`
  - `A|B|C`: `3`
- ArcadeDB `26.4.2`:
  - all four queries return `0`

This makes the boundary much clearer:

- single-label node pattern: correct
- label-disjunction node pattern: no rows

**Stronger variant from the feature-only random run**
The same family first surfaced in a larger path query:

```cypher
MATCH p = (n:A|B|C)-[:R*]->(m)
WHERE n.x = 5 AND (n.y > 5 OR n.y < 15)
RETURN last(relationships(p)) AS last_rel, n.x AS nx
ORDER BY nx;
```

Setup:

```cypher
CREATE (a:A {x: 5, y: 10}),
       (b:B {x: 5, y: 10}),
       (c:C {x: 5, y: 10}),
       (a)-[:R]->(b),
       (b)-[:R]->(c);
```

Observed results:

- Neo4j `2026.04.0`:
  - `3 rows`
- ArcadeDB `26.4.2`:
  - `0 rows`

Direct reduction shows that `last(relationships(p))` is not the core issue. The same zero-row behavior already appears in much simpler forms such as:

```cypher
MATCH p = (n:A|B|C)-[:R*]->(m)
RETURN count(*) AS cnt;
```

and even:

```cypher
MATCH (n:A|B|C)
RETURN count(*) AS cnt;
```

So the failure is not specific to path-return functions or variable-length traversal.
The deeper issue is that the node label disjunction pattern itself is evaluated incorrectly.

**Why this is kept separate**
This is intentionally kept separate from existing multi-label-node reports.

- existing multi-label reports in the repo concern nodes that already carry multiple labels
- this report is about the Cypher label-disjunction syntax in a node pattern, such as `(n:A|B)`

So the boundary is different.

**Verification environment**
Verified directly against:

- Neo4j Docker container: `neo4j:2026.04.0`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.4.2`
- local differential-testing harness
- direct fresh-container reruns

**Stage3 provenance**
This family was independently surfaced in the current feature-only random run as:

- Stage3 run: `historical_bug_queries_parallel_llm_v1_feature_only_random_20260511`
- Query ID: `RedisGraph__RedisGraph__2877__q005`
- Case ID: `case_8cc332f9093a`
- Stage3 artifact: `data/batch_runs/historical_bug_queries_parallel_llm_v1_feature_only_random_20260511/stage3x10/stage3_main/instantiated_cases.jsonl`
- Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_2026_04_0_arcadedb_26_4_2_feature_only_random_20260512b`
- Differential bucket: `all_passed_different_result`
