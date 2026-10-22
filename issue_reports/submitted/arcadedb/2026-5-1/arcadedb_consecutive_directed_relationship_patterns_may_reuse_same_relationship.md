done
**Suggested title**
`Consecutive directed relationship patterns may reuse the same relationship`

**ArcadeDB version**
Observed on Docker images:

- `arcadedata/arcadedb:26.4.1-SNAPSHOT`
- `arcadedata/arcadedb:26.4.2`

Not reproduced on:

- `arcadedata/arcadedb:26.3.2`

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
ArcadeDB may reuse the same directed relationship twice inside one matched pattern, even when both positions are explicitly directed and should require two distinct edges.

In the minimized repro below, the graph is:

```text
Alice <-[:KNOWS]- Bob -[:KNOWS]-> Charlie
```

For the pattern:

```cypher
(s:Person)<-[:KNOWS]-(f:Person)-[:KNOWS]->(d:Person)
```

Neo4j returns the two valid permutations:

- `Alice, Bob, Charlie`
- `Charlie, Bob, Alice`

ArcadeDB `26.4.1-SNAPSHOT` and `26.4.2` additionally return:

- `Alice, Bob, Alice`
- `Charlie, Bob, Charlie`

Those extra rows imply that the same `KNOWS` relationship is being reused on both sides of the middle node.

So this is not limited to the already-documented undirected case.
The same reuse bug also affects consecutive directed relationship slots.

**To Reproduce**

Setup:

```cypher
CREATE (:Person {name:'Alice'})<-[:KNOWS]-(:Person {name:'Bob'})-[:KNOWS]->(:Person {name:'Charlie'});
```

Query:

```cypher
MATCH (s:Person)<-[:KNOWS]-(f:Person)-[:KNOWS]->(d:Person)
RETURN s.name AS s, f.name AS f, d.name AS d
ORDER BY s, f, d;
```

**Expected behavior**
Observed Neo4j result:

```text
Alice,   Bob, Charlie
Charlie, Bob, Alice
```

Only those two rows should match.

**Actual behavior**
Observed ArcadeDB `26.4.1-SNAPSHOT` and `26.4.2` results:

```text
Alice,   Bob, Alice
Alice,   Bob, Charlie
Charlie, Bob, Alice
Charlie, Bob, Charlie
```

So ArcadeDB produces two spurious rows where the same relationship is reused.

**Control cases**

On `26.3.2`, the same query behaves correctly:

```text
Alice,   Bob, Charlie
Charlie, Bob, Alice
```

If distinctness is enforced explicitly, ArcadeDB `26.4.1-SNAPSHOT` and `26.4.2` also behave correctly:

```cypher
MATCH (s:Person)<-[r1:KNOWS]-(f:Person)-[r2:KNOWS]->(d:Person)
WHERE r1 <> r2
RETURN s.name AS s, f.name AS f, d.name AS d
ORDER BY s, f, d;
```

Observed result on Neo4j and all tested ArcadeDB versions:

```text
Alice,   Bob, Charlie
Charlie, Bob, Alice
```

So the underlying graph is fine.
The bad rows only appear when ArcadeDB is allowed to choose relationships for the two directed pattern positions on its own.

The same issue also appears in the converging form:

```cypher
CREATE (:Person {name:'Alice'})-[:KNOWS]->(:Person {name:'Bob'})<-[:KNOWS]-(:Person {name:'Charlie'});

MATCH (p1:Person)-[:KNOWS]->(p2:Person)<-[:KNOWS]-(p3:Person)
RETURN p1.name AS p1, p2.name AS p2, p3.name AS p3
ORDER BY p1, p2, p3;
```

Observed results:

- Neo4j:

```text
Alice,   Bob, Charlie
Charlie, Bob, Alice
```

- ArcadeDB `26.4.1-SNAPSHOT` / `26.4.2`:

```text
Alice,   Bob, Alice
Alice,   Bob, Charlie
Charlie, Bob, Alice
Charlie, Bob, Charlie
```

So this is not just one directional orientation.
ArcadeDB can reuse the same edge across consecutive directed slots in both diverging and converging shapes.

The same current-version family also appears when one slot is a variable-length directed expansion and the adjacent slot is another directed edge from the same start node:

```cypher
CREATE (a:Person {name:'Alice'}),
       (b:Person {name:'Bob'}),
       (c:Person {name:'Charlie'}),
       (a)-[:KNOWS]->(b),
       (b)-[:KNOWS]->(c),
       (a)-[:KNOWS]->(c);

MATCH (a:Person)-[:KNOWS*1..2]->(b:Person),
      (a)-[:KNOWS]->(c:Person)
RETURN a.name AS a, b.name AS b, c.name AS c
ORDER BY a, b, c;
```

Observed results:

- Neo4j:

```text
Alice, Bob,     Charlie
Alice, Charlie, Bob
Alice, Charlie, Charlie
```

- ArcadeDB `26.4.2`:

```text
Alice, Bob,     Bob
Alice, Bob,     Charlie
Alice, Charlie, Bob
Alice, Charlie, Bob
Alice, Charlie, Charlie
Alice, Charlie, Charlie
Bob,   Charlie, Charlie
```

So the same relationship-reuse problem is not limited to two fixed-length adjacent slots.
ArcadeDB can also reuse edges across a variable-length directed slot and a neighboring directed slot.

If the relationship slots are made explicit, the extra rows disappear:

```cypher
MATCH (a:Person)-[r1:KNOWS*1..2]->(b:Person),
      (a:Person)-[r3:KNOWS]->(c:Person)
RETURN a.name AS a, b.name AS b, c.name AS c
ORDER BY a, b, c;
```

Observed result on Neo4j and ArcadeDB `26.4.2`:

```text
Alice, Bob,     Charlie
Alice, Charlie, Bob
Alice, Charlie, Charlie
```

And an explicit uniqueness guard also restores agreement:

```cypher
MATCH p = (a:Person)-[:KNOWS*1..2]->(b:Person),
      (a:Person)-[r:KNOWS]->(c:Person)
WHERE none(rel IN relationships(p) WHERE rel = r)
RETURN a.name AS a, b.name AS b, c.name AS c
ORDER BY a, b, c;
```

Observed result on Neo4j and ArcadeDB `26.4.2`:

```text
Alice, Bob,     Charlie
Alice, Charlie, Bob
Alice, Charlie, Charlie
```

**Aggregation fallout**
This also corrupts aggregates built on top of the duplicated matches.

For example:

```cypher
MATCH (p1:Person)-[:KNOWS]->(p2:Person)<-[:KNOWS]-(p3:Person)
RETURN p2.name AS common_friend, count(p1) AS friend_count;
```

Observed results:

- Neo4j: `Bob, 2`
- ArcadeDB `26.4.1-SNAPSHOT`: `Bob, 4`
- ArcadeDB `26.4.2`: `Bob, 4`

And:

```cypher
MATCH (s:Person)<-[:KNOWS]-(f:Person)-[:KNOWS]->(d:Person)
RETURN count(d) AS c;
```

Observed results:

- Neo4j: `2`
- ArcadeDB `26.4.1-SNAPSHOT`: `4`
- ArcadeDB `26.4.2`: `4`

So the bug is not just a row-shape problem.
It can silently double later counts.

**Why this is kept separate**
This is related to the submitted undirected family:

- [arcadedb_consecutive_undirected_relationship_patterns_may_reuse_same_relationship.md](D:/sci/works/get%20bugs/graph-bug-fuzz-mvp/issue_reports/submitted/arcadedb/2026-5-1/arcadedb_consecutive_undirected_relationship_patterns_may_reuse_same_relationship.md)

But the boundary is different.
That report is about undirected consecutive relationship patterns.
This report shows the same reuse bug even when both positions are directed, so it is kept separate.

**Differential seeds**
This family surfaced while minimizing two skeleton-run seeds:

- `RedisGraph__RedisGraph__278__q001`
- `FalkorDB__FalkorDB__1844__q003`

Original generated queries:

```cypher
MATCH (s:Person)<-[:KNOWS]-(f:Person)-[:KNOWS]->(d:Person)
RETURN count(d)
```

and:

```cypher
MATCH (p1:Person)-[:KNOWS]->(p2:Person)<-[:KNOWS]-(p3:Person)
RETURN p2.name AS common_friend, count(p1) AS friend_count
```

Both reduce to the same root issue: ArcadeDB can reuse one directed `KNOWS` relationship to satisfy both consecutive slots.

The same family was independently resurfaced again in the feature-only random differential:

- `RedisGraph__RedisGraph__1619__q003`

Original generated query:

```cypher
MATCH (a:Person)-[:KNOWS*1..2]->(b:Person), (a)-[:KNOWS]->(c:Person)
WITH a, b, c, CASE WHEN a.name = 'Alice' THEN 'friend' ELSE 'acquaintance' END AS relation
RETURN relation, count(*) AS cnt
UNION ALL
MATCH (x:Person)-[:KNOWS*1..2]->(y:Person)
RETURN x.name AS relation, count(*) AS cnt
```

Direct reduction shows the root cause is already present before the `UNION ALL`.
The first `MATCH` alone already produces impossible combinations because ArcadeDB reuses directed `KNOWS` edges across adjacent slots.

**Verification environment**
Verified directly against:

- Neo4j Docker container: `neo4j:latest`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.3.2`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.4.1-SNAPSHOT`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.4.2`
- Local differential-testing harness
- Direct ArcadeDB HTTP execution using the same request mode as Studio

**Stage3 provenance**
- Stage3 run: `historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b`
- Stage3 artifact: `data/batch_runs/historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b/stage3_main/instantiated_cases.jsonl`
- Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_arcadedb_skeleton_20260430`
- Differential bucket: `all_passed_different_result`
- Example source queries from this run:
  - `RedisGraph__RedisGraph__278__q001` / `case_4f5956205808`
  - `FalkorDB__FalkorDB__1844__q003` / `case_b075455395a0`

The same family was independently resurfaced again in the feature-only random differential:

- Stage3 run: `historical_bug_queries_parallel_llm_v1_feature_only_random_20260506`
- Query ID: `RedisGraph__RedisGraph__1619__q003`
- Case ID: `case_1bfba45ff9ad`
- Stage3 artifact: `data/batch_runs/historical_bug_queries_parallel_llm_v1_feature_only_random_20260506/stage3x10/stage3_main/instantiated_cases.jsonl`
- Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_arcadedb_26_4_2_feature_only_random_20260507`
- Differential bucket: `all_passed_different_result`
