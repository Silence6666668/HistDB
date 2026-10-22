done
**Suggested title**
`allReduce(...) may evaluate true cases as false`

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
ArcadeDB may evaluate `allReduce(...)` incorrectly and return `false` even when every iteration satisfies the predicate.

In the minimized repro below, the accumulator takes values `1`, `3`, and `6`.
All of those values satisfy `x > 0` for the corresponding element, so the result should be `true`.
Neo4j returns `true`.
ArcadeDB returns `false`.

**To Reproduce**

Query:

```cypher
RETURN allReduce(acc = 0, x IN [1, 2, 3] | acc + x, x > 0) AS result;
```

**Expected behavior**
Observed Neo4j result:

```text
true
```

**Actual behavior**
Observed ArcadeDB result:

```text
false
```

**Control cases**

Plain `reduce(...)` works correctly on both engines:

```cypher
RETURN reduce(acc = 0, x IN [1, 2, 3] | acc + x) AS total;
```

Observed result:

```text
6
```

Plain `all(...)` also works correctly on both engines:

```cypher
RETURN all(x IN [1, 2, 3] WHERE x > 0) AS result;
```

Observed result:

```text
true
```

So the issue is not a general `reduce(...)` problem, and it is not a general `all(...)` problem. The failure is specific to `allReduce(...)`.

**Additional controls**

If the predicate is actually false for every iteration, both engines return `false`:

```cypher
RETURN allReduce(acc = 0, x IN [1, 2, 3] | acc + x, x > 10) AS result;
```

Observed result on both engines:

```text
false
```

If the list is empty, Neo4j returns vacuous truth, but ArcadeDB still returns `false`:

```cypher
RETURN allReduce(acc = 0, x IN [] | acc + x, x > 0) AS result;
```

Observed results:

- Neo4j: `true`
- ArcadeDB: `false`

This suggests ArcadeDB may be using the wrong default truth value for `allReduce(...)`, or otherwise evaluating the construct against the wrong state.

If the predicate depends on the accumulator instead, both engines can still agree:

```cypher
RETURN allReduce(acc = 0, x IN [1, 2, 3] | acc + x, acc >= 0) AS result;
```

Observed result on both engines:

```text
true
```

So the failure is not that `allReduce(...)` is completely unsupported. It appears to mis-evaluate at least some valid predicate forms.

**Why this is kept separate**
This is separate from ArcadeDB's list-predicate bugs in `WHERE`.

- the `single(...)` candidate is about row filtering in `WHERE`
- the older `any(...)` issue is also about `WHERE`
- this candidate is a pure expression-level wrong result in `RETURN`

So this is intentionally kept separate.

**Differential seed**
This family surfaced while minimizing:

- `ArcadeData__arcadedb__3424__q001`

**Stage3 provenance**
- Stage3 run: `historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b`
- Stage3 file: `data/batch_runs/historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b/stage3_main/instantiated_cases.jsonl`
- Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_arcadedb_skeleton_20260430`
- Differential bucket: `all_passed_different_result`
- Example case id: `case_7720bb1cb73f`
