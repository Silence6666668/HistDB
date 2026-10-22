done
**Suggested title**
`CALL subquery SET may leave the carried outer variable stale`

**ArcadeDB version**
Observed on Docker images:

- `arcadedata/arcadedb:latest`
- `arcadedata/arcadedb:26.3.2`
- `arcadedata/arcadedb:26.4.1-SNAPSHOT`
- `arcadedata/arcadedb:26.4.2`

The same result was also rechecked on a fresh `26.4.2` container.

**Discovered on**
- `2026-05-07`

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
If a `CALL` subquery updates a node through a carried outer variable, ArcadeDB may keep the outer variable stale after the subquery returns.

In the minimized repro below, the node is clearly updated inside the subquery:

```cypher
SET n.score = 2
```

Neo4j then exposes the updated value through the same outer variable `n`.
ArcadeDB keeps returning the old value through `n`, even though the update is already visible to a fresh re-match.

So this is not a general write-failure bug.
It looks more like the outer variable keeps an old snapshot across the `CALL` boundary.

**To Reproduce**

Setup:

```cypher
CREATE (:Node {score: 1});
```

Query:

```cypher
MATCH (n:Node)
CALL {
  WITH n
  SET n.score = 2
  RETURN 1 AS ok
}
RETURN n.score AS score;
```

**Expected behavior**
Observed Neo4j result:

```text
2
```

ArcadeDB should return the same updated value.

**Actual behavior**
Observed ArcadeDB result on all tested versions:

```text
1
```

So the write happens inside the subquery, but the carried outer variable still exposes the old property value.

**Control cases**

A plain in-query `SET` without the `CALL` subquery behaves correctly:

```cypher
MATCH (n:Node)
SET n.score = 2
RETURN n.score AS score;
```

Observed result on Neo4j and all tested ArcadeDB versions:

```text
2
```

If the updated node is returned explicitly from the subquery, ArcadeDB is also correct:

```cypher
MATCH (n:Node)
CALL {
  WITH n
  SET n.score = 2
  RETURN n AS updated
}
RETURN updated.score AS score;
```

Observed result on Neo4j and all tested ArcadeDB versions:

```text
2
```

If the node is re-matched after the subquery, ArcadeDB again sees the update:

```cypher
MATCH (n:Node)
CALL {
  WITH n
  SET n.score = 2
  RETURN 1 AS ok
}
WITH count(*) AS _
MATCH (m:Node)
RETURN m.score AS score;
```

Observed result on Neo4j and all tested ArcadeDB versions:

```text
2
```

So the update is not lost.
The bad behavior is specifically that the carried outer variable `n` remains stale after the subquery returns.

**Stronger current differential variant**

The same family surfaced in the current feature-only random differential in a noisier vector-property shape:

Setup:

```cypher
CREATE (:TestNode {id: 1, vector: [1.0, 2.0, 3.0]});
```

Query:

```cypher
MATCH (n:TestNode)
CALL {
  WITH n
  SET n.vector = [4.0, 5.0, 6.0]
} IN TRANSACTIONS OF 1000 ROWS
RETURN n;
```

Observed results:

- Neo4j:

```text
{id: 1, vector: [4.0, 5.0, 6.0]}
```

- ArcadeDB `26.4.2`:

```text
{id: 1, vector: [1.0, 2.0, 3.0]}
```

Direct minimization shows that `IN TRANSACTIONS` is not required for reproduction.
The smaller root cause is already visible with an ordinary `CALL` subquery and a scalar property.

**Additional current-version supporting repros**

The same stale-outer-variable behavior also shows up with a computed update:

```cypher
CREATE (:Person {name:'Alice', age:30}),
       (:Person {name:'Charlie', age:35});

MATCH (p:Person)
CALL {
  WITH p
  SET p.score = p.age * 2
  RETURN 1 AS ok
}
RETURN count(*) AS cnt, collect(p.score) AS scores;
```

Observed results on a fresh `26.4.2` container:

- Neo4j:

```text
2, [60, 70]
```

- ArcadeDB:

```text
2, []
```

So the subquery write is happening, but the carried outer variable still exposes stale property values to the aggregate.

The current feature-only random differential also produced a stronger user-visible variant:

```cypher
MATCH (p:Person)
WITH p
ORDER BY p.age DESC
LIMIT 2
CALL {
  WITH p
  SET p.score = p.age * 2
}
RETURN p.name AS name, p.score AS score
ORDER BY score
LIMIT 1;
```

Observed results:

- Neo4j:

```text
Alice, 60
```

- ArcadeDB fresh `26.4.2`:

```text
Charlie, null
```

That result is consistent with the same root cause: the post-subquery pipeline is still reading stale outer-variable snapshots instead of the updated node state.

**Why this is kept separate**
This is related to:

- [arcadedb_set_may_leave_other_aliases_to_same_node_stale.md](D:/sci/works/get%20bugs/graph-bug-fuzz-mvp/issue_reports/submitted/arcadedb/2026-4-24/arcadedb_set_may_leave_other_aliases_to_same_node_stale.md)

but the boundary is different.

That report is about two variables that are already bound to the same node before the write.
This report reproduces with only one outer variable and a `CALL` subquery.
The stale snapshot appears across the subquery boundary itself.

So this is kept separate.

**Verification environment**
Verified directly against:

- Neo4j Docker container: `neo4j:latest`
- ArcadeDB Docker container: `arcadedata/arcadedb:latest`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.3.2`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.4.1-SNAPSHOT`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.4.2`
- Fresh ArcadeDB Docker container: `arcadedata/arcadedb:26.4.2`
- Local differential-testing harness
- Direct ArcadeDB HTTP execution using the same request mode as Studio

**Stage3 provenance**
- Stage3 run: `historical_bug_queries_parallel_llm_v1_feature_only_random_20260506`
- Stage3 artifact: `data/batch_runs/historical_bug_queries_parallel_llm_v1_feature_only_random_20260506/stage3x10/stage3_main/instantiated_cases.jsonl`
- Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_arcadedb_26_4_2_feature_only_random_20260507`
- Differential bucket: `all_passed_different_result`
- Example source query from this run:
  - `neo4j__13259__q001` / `case_0021acbd2ca2`
- Additional current-version supporting seed:
  - `FalkorDB__FalkorDB__309__q007` / `case_862071819d5e`
