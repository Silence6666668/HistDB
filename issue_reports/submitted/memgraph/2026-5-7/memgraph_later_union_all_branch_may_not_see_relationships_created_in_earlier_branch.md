**Suggested title**
`A later UNION ALL branch may not see relationships created in an earlier branch`

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
Memgraph may fail to expose a relationship created in an earlier `UNION ALL` branch to a later branch in the same query.

In the minimized repro below, the first branch creates a negative-`since` `KNOWS` relationship from `Alice` to `Bob`.
The later branch then scans for exactly that relationship.

Neo4j returns two rows:

```text
Alice, Bob
Alice, Bob
```

The first row comes from the write branch and the second from the later read branch.

Memgraph returns only one row:

```text
Alice, Bob
```

So the later `UNION ALL` branch does not appear to see the relationship that was created by the earlier branch.

This does not look like a generic same-query write-visibility problem, because the same write followed by a later `MATCH` works correctly outside `UNION ALL`.

**To Reproduce**
1. Start the Memgraph Docker container.
2. Run:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice'}), (:Person {name:'Bob'});

MATCH (a:Person {name:'Alice'}), (b:Person {name:'Bob'})
MERGE (a)-[:KNOWS {since:-1}]->(b)
RETURN a.name, b.name
UNION ALL
MATCH (a:Person)-[r:KNOWS]->(b:Person)
WHERE r.since < 0
RETURN a.name, b.name;
```

**Expected behavior**
Observed Neo4j result:

```text
Alice, Bob
Alice, Bob
```

Memgraph should return the same two rows.

**Actual behavior**
Observed Memgraph result:

```text
Alice, Bob
```

The later `UNION ALL` branch does not see the freshly created negative-`since` relationship.

**Control query 1**
If the same write is followed by a later `MATCH` without `UNION ALL`, both engines behave correctly:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice'}), (:Person {name:'Bob'});

MATCH (a:Person {name:'Alice'}), (b:Person {name:'Bob'})
MERGE (a)-[:KNOWS {since:-1}]->(b)
WITH 1 AS _
MATCH (a:Person)-[r:KNOWS]->(b:Person)
WHERE r.since < 0
RETURN a.name, b.name;
```

Observed result on both Neo4j and Memgraph:

```text
Alice, Bob
```

So this is not a blanket "same-query write then read" failure.

**Control query 2**
If branch labels are added, Neo4j shows that both branches contribute a row:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice'}), (:Person {name:'Bob'});

MATCH (a:Person {name:'Alice'}), (b:Person {name:'Bob'})
MERGE (a)-[:KNOWS {since:-1}]->(b)
RETURN 'write' AS branch, a.name, b.name
UNION ALL
MATCH (a:Person)-[r:KNOWS]->(b:Person)
WHERE r.since < 0
RETURN 'read' AS branch, a.name, b.name;
```

Observed results:

- Neo4j:

```text
write, Alice, Bob
read,  Alice, Bob
```

- Memgraph:

```text
write, Alice, Bob
```

So the later read branch is the one that disappears.

**Additional supporting repro**
The same family also appears with a later branch that scans for the same relationship without any `since < 0` filter:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice'}), (:Person {name:'Bob'});

MATCH (a:Person {name: 'Alice'}), (b:Person {name: 'Bob'})
MERGE (a)-[r:KNOWS {since: 2020}]->(b)
SET r.years = 4
RETURN a.name AS a, b.name AS b, r.since AS s, r.years AS y
UNION ALL
MATCH (a:Person {name: 'Alice'})-[r:KNOWS]->(b:Person {name: 'Bob'})
RETURN a.name AS a, b.name AS b, r.since AS s, r.years AS y;
```

Observed results:

- Neo4j:

```text
Alice, Bob, 2020, 4
Alice, Bob, 2020, 4
```

- Memgraph:

```text
Alice, Bob, 2020, 4
```

If the same two steps are executed as separate queries, both engines return the second row normally. So this is consistent with the earlier repro: the later `UNION ALL` branch fails to observe the relationship created by the earlier branch.

**Why this looks separate from the existing node-visibility report**
This is closely related to the submitted node-visibility family:

- [memgraph_later_union_branch_may_not_see_nodes_created_in_earlier_branch.md](D:/sci/works/get%20bugs/graph-bug-fuzz-mvp/issue_reports/submitted/memgraph/2026-4-30/memgraph_later_union_branch_may_not_see_nodes_created_in_earlier_branch.md)

But the boundary here is narrower and cleaner:

- both endpoints already exist before the query starts
- only the relationship is created in the first branch
- the later branch specifically fails to see that new relationship

So this looks like the relationship analogue of the earlier node-visibility issue, and it is kept separate.

**Differential seed**
This family surfaced while minimizing:

- `FalkorDB__FalkorDB__1183__q002`
- `case_f5e429a3f230`

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
- Differential bucket: `all_passed_different_result`
- Supporting source query:
  - `FalkorDB__FalkorDB__1183__q002` / `case_f5e429a3f230`
  - `RedisGraph__RedisGraph__924__q004` / `case_dac029db6abd`
