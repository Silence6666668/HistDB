done
**Suggested title**
`IN predicate with duplicate string literals may duplicate matching rows`

**Memgraph version**
Observed on:

- Memgraph `3.9.0`

Docker image used locally:
- `memgraph/memgraph:3.9.0`

**Discovered on**
- `2026-05-07`

**Environment**
- Host OS: Windows
- Architecture: x86_64
- Deployment: Docker
- Connection method: Bolt
- Client used for direct reproduction: repo Stage4 executor using Python `neo4j` Bolt driver, plus direct Bolt execution
- Differential comparison target: Neo4j Community `2026.04.0`

**Describe the bug**
Memgraph may duplicate rows when an `IN` predicate contains the same string literal more than once.

In the minimized repro below, the graph contains exactly one `Alice`, one `Bob`, and one `Charlie`.

Neo4j returns:

```text
Alice
Bob
```

Memgraph returns:

```text
Alice
Alice
Bob
```

So the repeated `'Alice'` literal in the right-hand list appears to cause the same matching row to be emitted twice.

This does not look like a generic `IN` problem, because the same query works correctly when the duplicate string is removed, and a numeric control with duplicated integers also behaves correctly.

**To Reproduce**
1. Start the Memgraph Docker container.
2. Run:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice'}), (:Person {name:'Bob'}), (:Person {name:'Charlie'});

MATCH (q:Person)
WHERE q.name IN ['Alice', 'Alice', 'Bob']
RETURN q.name AS name
ORDER BY name;
```

**Expected behavior**
Observed Neo4j result:

```text
Alice
Bob
```

Memgraph should return the same two rows.

**Actual behavior**
Observed Memgraph result:

```text
Alice
Alice
Bob
```

The row for `Alice` is duplicated.

**Control query 1**
If the duplicate string literal is removed, both engines behave correctly:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice'}), (:Person {name:'Bob'}), (:Person {name:'Charlie'});

MATCH (q:Person)
WHERE q.name IN ['Alice', 'Bob']
RETURN q.name AS name
ORDER BY name;
```

Observed result on both Neo4j and Memgraph:

```text
Alice
Bob
```

So the issue is not a generic string-`IN` failure.

**Control query 2**
A numeric duplicate-list control does not reproduce the same problem:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Item {id:1}), (:Item {id:2}), (:Item {id:3});

MATCH (n:Item)
WHERE n.id IN [1, 1, 2]
RETURN n.id AS id
ORDER BY id;
```

Observed result on both Neo4j and Memgraph:

```text
1
2
```

So the bad behavior appears narrower than "duplicate elements on the right side of `IN` always duplicate rows".

**How this surfaced**
This family surfaced while minimizing:

- `FalkorDB__FalkorDB__164__q006`
- `case_fbcde9409952`

The original differential query mixed:

- `WITH ... ORDER BY ... LIMIT`
- a later `MATCH`
- and a duplicate-string `IN` list

The original full query did not remain stable after minimization, but it led directly to the cleaner core issue above:
Memgraph can duplicate matches for repeated string literals inside `IN`.

**Verification Environment**
Plain Memgraph Docker image is enough.

Preferred verification target:
- Docker image
- x86_64
- `memgraph/memgraph`

I can verify a fix using:
- plain Memgraph Docker image on Windows host
- repo Stage4 executor / direct Bolt execution

**Stage3 provenance**
- Stage3 run: `historical_bug_queries_parallel_llm_v1_feature_only_random_20260506`
- Stage3 artifact: `data/batch_runs/historical_bug_queries_parallel_llm_v1_feature_only_random_20260506/stage3x10/stage3_main/instantiated_cases.jsonl`
- Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_memgraph_feature_only_random_20260507`
- Original supporting source query:
  - `FalkorDB__FalkorDB__164__q006` / `case_fbcde9409952`
