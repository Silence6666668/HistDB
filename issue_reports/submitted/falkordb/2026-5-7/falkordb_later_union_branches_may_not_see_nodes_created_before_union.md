**Suggested title**
`Later UNION branches may not see nodes created earlier in the same query`

**FalkorDB version**
Observed on:

- FalkorDB `4.18.3`
- FalkorDB `4.18.4`

Docker images used locally:
- `falkordb/falkordb:v4.18.3`
- `falkordb/falkordb:v4.18.4`

**Discovered on**
- `2026-05-07`

**Environment**
- Host OS: Windows
- Architecture: x86_64
- Deployment: Docker
- Connection method: RESP / `GRAPH.QUERY`
- Client used for direct reproduction: direct `redis-cli GRAPH.QUERY` and project differential harness
- Differential comparison target: Neo4j Docker target from the local harness

**Describe the bug**
FalkorDB may let only the first `UNION` branch see nodes that were created earlier in the same query.

In the minimized repro below, the query first creates two `Person` nodes, then splits into two branches:

- one branch returns the person with `age > 25`
- the other branch returns the person with `age <= 25`

Neo4j returns both rows:

```text
Alice
Bob
```

FalkorDB returns only:

```text
Alice
```

So the node created earlier in the query is visible to the first branch but not to the later branch.

**To Reproduce**
1. Start the FalkorDB Docker container.
2. Run:

```cypher
CREATE (:Person {name: 'Alice', age: 30}), (:Person {name: 'Bob', age: 25})
WITH count(*) AS dummy
MATCH (n:Person) WHERE n.age > 25
RETURN n.name AS name
UNION
MATCH (n:Person) WHERE n.age <= 25
RETURN n.name AS name;
```

**Expected behavior**
Neo4j returns:

```text
Alice
Bob
```

FalkorDB should return the same two rows.

**Actual behavior**
Observed FalkorDB result:

```text
Alice
```

The later `UNION` branch does not return `Bob`, even though `Bob` was created earlier in the same query.

**Control query 1**
This is not a generic write-visibility problem for nodes. If `UNION` is removed, FalkorDB sees both created nodes:

```cypher
CREATE (:Person {name: 'Alice', age: 30}), (:Person {name: 'Bob', age: 25})
WITH count(*) AS dummy
MATCH (n:Person)
RETURN n.name AS name
ORDER BY name;
```

Observed result on both Neo4j and FalkorDB:

```text
Alice
Bob
```

So nodes created earlier in the query are not invisible in general.

**Control query 2**
This is also not a generic `UNION` problem. If the same two nodes already exist before the query starts, both engines return both rows:

```cypher
MATCH (n:Person) WHERE n.age > 25
RETURN n.name AS name
UNION
MATCH (n:Person) WHERE n.age <= 25
RETURN n.name AS name;
```

Observed result on both Neo4j and FalkorDB after pre-creating `Alice` and `Bob`:

```text
Alice
Bob
```

So read-only `UNION` behaves correctly.

**Control query 3**
The same issue also appears with `UNION ALL`, so this is not a deduplication bug:

```cypher
CREATE (:Person {name: 'Alice', age: 30}), (:Person {name: 'Bob', age: 25})
WITH count(*) AS dummy
MATCH (n:Person) WHERE n.age > 25
RETURN n.name AS name
UNION ALL
MATCH (n:Person) WHERE n.age <= 25
RETURN n.name AS name;
```

Observed results:

- Neo4j:

```text
Alice
Bob
```

- FalkorDB `4.18.3`:

```text
Alice
```

So the problem is not that `UNION` is deduplicating too aggressively. The later branch is simply not seeing the earlier write.

**Control query 4**
If the branch order is reversed, FalkorDB returns only the row from the new first branch:

```cypher
CREATE (:Person {name: 'Alice', age: 30}), (:Person {name: 'Bob', age: 25})
WITH count(*) AS dummy
MATCH (n:Person) WHERE n.age <= 25
RETURN n.name AS name
UNION
MATCH (n:Person) WHERE n.age > 25
RETURN n.name AS name;
```

Observed results:

- Neo4j:

```text
Bob
Alice
```

- FalkorDB `4.18.3`:

```text
Bob
```

This makes the boundary much clearer: FalkorDB behaves as if only the first `UNION` branch can see nodes created before the `UNION`.

**Why this looks like a real bug**
The behavior is narrow and consistent:

- node creation before `UNION`: broken
- plain later `MATCH` without `UNION`: correct
- read-only `UNION`: correct
- `UNION ALL`: also broken
- reversing branch order flips which single row survives

So this does not look like a generic parser or `UNION` formatting issue. It looks like later `UNION` branches are losing visibility of writes performed earlier in the same query.

**Differential seed**
This family surfaced while minimizing:

- `RedisGraph__RedisGraph__548__q002`

**Verification Environment**
Plain FalkorDB Docker image is enough.

Preferred verification target:
- Docker image
- x86_64
- `falkordb/falkordb`

I can verify a fix using:
- plain FalkorDB Docker image on Windows host
- direct `redis-cli GRAPH.QUERY`
- project differential harness

**Stage3 provenance**
- Stage3 run: `historical_bug_queries_parallel_llm_v1_feature_only_random_20260506`
- Stage3 artifact: `data/batch_runs/historical_bug_queries_parallel_llm_v1_feature_only_random_20260506/stage3x10/stage3_main/instantiated_cases.jsonl`
- Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_falkordb_4_18_3_feature_only_random_20260507`
- Differential bucket: `all_passed_different_result`
- Example source query:
  - `RedisGraph__RedisGraph__548__q002` / `case_81e2a0bb5cdb`
