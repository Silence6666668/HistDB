**Suggested title**
`Anonymous undirected relationship pattern may collapse parallel edges between the same endpoints`

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
- Client used for direct reproduction: project FalkorDB executor and direct local reproduction
- Differential comparison target: Neo4j Docker target from the local harness

**Describe the bug**
FalkorDB may collapse multiple matched relationships down to one row when an undirected relationship slot is anonymous and the matched edges connect the same endpoints.

In the minimized repro below, the graph contains two separate `[:KNOWS]` relationships between `Alice` and `Bob`.

Neo4j treats both relationships as matched rows and returns:

```text
cnt = 2
```

FalkorDB `4.18.3` returns:

```text
cnt = 1
```

So FalkorDB appears to collapse parallel matches between the same endpoints when the relationship itself is not exposed.

**To Reproduce**
1. Start the FalkorDB Docker container.
2. Run:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Person {name:'Alice'}),
       (b:Person {name:'Bob'}),
       (a)-[:KNOWS]->(b),
       (a)-[:KNOWS]->(b);

MATCH (:Person {name:'Alice'})-[:KNOWS]-(:Person {name:'Bob'})
RETURN count(*) AS cnt;
```

**Expected behavior**
Neo4j returns:

```text
cnt = 2
```

FalkorDB should return the same result.

**Actual behavior**
Observed FalkorDB `4.18.3` result:

```text
cnt = 1
```

So the two parallel `[:KNOWS]` relationships are collapsed into a single matched row.

**Control query 1**
The same collapse is visible even without aggregation. If the query only projects endpoint values, Neo4j returns two identical rows while FalkorDB returns one:

```cypher
MATCH (p:Person {name:'Alice'})-[:KNOWS]-(friend:Person {name:'Bob'})
RETURN p.name, friend.name;
```

Observed results:

- Neo4j:

```text
Alice, Bob
Alice, Bob
```

- FalkorDB `4.18.3`:

```text
Alice, Bob
```

So this is not just an aggregation bug. The underlying matched rows are already being collapsed.

**Control query 2**
The relationships do still exist as distinct edges. If the relationship variable is explicitly used in the aggregation, both engines count both edges:

```cypher
MATCH (p:Person {name:'Alice'})-[r:KNOWS]-(friend:Person {name:'Bob'})
RETURN count(*) AS cnt, count(r) AS cr;
```

Observed result on both Neo4j and FalkorDB:

```text
cnt = 2
cr  = 2
```

So the bug is not that one relationship is missing from the graph. The collapse happens specifically when the relationship slot is anonymous and only endpoint bindings survive.

**Control query 3**
The same collapse also appears when the two `[:KNOWS]` relationships go in opposite directions instead of the same direction:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Person {name:'Alice'}),
       (b:Person {name:'Bob'}),
       (a)-[:KNOWS]->(b),
       (b)-[:KNOWS]->(a);

MATCH (:Person {name:'Alice'})-[:KNOWS]-(:Person {name:'Bob'})
RETURN count(*) AS cnt;
```

Observed results:

- Neo4j:

```text
cnt = 2
```

- FalkorDB `4.18.3`:

```text
cnt = 1
```

So the issue is not tied to one orientation. FalkorDB can collapse both same-direction and opposite-direction parallel matches between the same endpoints.

**Original differential shape**
The same family surfaced in a larger current-version query:

```cypher
MATCH (p:Person)-[:KNOWS]-(friend:Person)
WHERE p.name = 'Alice' AND friend.age > 20
WITH p, friend
MATCH (p)-[:LIVES_IN]->(city:City)
RETURN p.name, friend.name, city.name;
```

Setup:

```cypher
CREATE (a:Person {name: 'Alice', age: 30}),
       (b:Person {name: 'Bob', age: 25}),
       (c:City {name: 'New York'}),
       (a)-[:KNOWS]->(b),
       (b)-[:KNOWS]->(a),
       (a)-[:LIVES_IN]->(c),
       (b)-[:LIVES_IN]->(c);
```

Observed results:

- Neo4j:

```text
Alice, Bob, New York
Alice, Bob, New York
```

- FalkorDB `4.18.3`:

```text
Alice, Bob, New York
```

So the larger diff is explained by the same earlier collapse of parallel undirected matches.

**Why this is kept separate**
This is related to:

- [falkordb_grouped_aggregation_may_collapse_parallel_relationships_between_same_endpoints.md](D:/sci/works/get%20bugs/graph-bug-fuzz-mvp/issue_reports/submitted/falkordb/2026-4-29/falkordb_grouped_aggregation_may_collapse_parallel_relationships_between_same_endpoints.md)

But the boundary is different.
That report is about grouped aggregation collapsing parallel relationships.
This report shows that FalkorDB can already collapse the underlying matched rows even before grouping, as long as an anonymous undirected relationship slot produces identical endpoint bindings.

**Differential seed**
This family surfaced while minimizing:

- `FalkorDB__FalkorDB__883__q004`

**Verification Environment**
Plain FalkorDB Docker image is enough.

Preferred verification target:
- Docker image
- x86_64
- `falkordb/falkordb`

I can verify a fix using:
- plain FalkorDB Docker image on Windows host
- direct local reproduction
- project differential harness

**Stage3 provenance**
- Stage3 run: `historical_bug_queries_parallel_llm_v1_feature_only_random_20260506`
- Stage3 artifact: `data/batch_runs/historical_bug_queries_parallel_llm_v1_feature_only_random_20260506/stage3x10/stage3_main/instantiated_cases.jsonl`
- Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_falkordb_4_18_3_feature_only_random_20260507`
- Differential bucket: `all_passed_different_result`
- Example source query:
  - `FalkorDB__FalkorDB__883__q004` / `case_b737fd76755c`
