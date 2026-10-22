done
**Suggested title**
`shortestPath/allShortestPaths with variable-length relationship type alternation may match no rows`

**ArcadeDB version**
Observed on Docker images:

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
ArcadeDB may silently return no rows when `shortestPath(...)` or `allShortestPaths(...)` uses a variable-length relationship pattern whose type is an alternation, such as:

```cypher
[:R1|R2*]
```

This does not look like a general shortest-path failure:

- the same variable-length pattern works in an ordinary `MATCH`
- the same shortest-path call works with a single relationship type such as `[:R1*]`
- fixed-length type alternation such as `[:R1|R2]` also works

The failure starts specifically when shortest-path evaluation is combined with both:

- relationship type alternation, and
- variable length

**To Reproduce**

Setup:

```cypher
CREATE (:Person {name:'Alice'})-[:R1]->(:Mid {name:'M'})-[:R2]->(:City {name:'X'});
```

Query:

```cypher
MATCH p = allShortestPaths((:Person)-[:R1|R2*]->(:City))
RETURN size(nodes(p)) AS pathLen;
```

**Expected behavior**
Observed Neo4j result:

```text
3
```

ArcadeDB should return the same single row.

**Actual behavior**
Observed ArcadeDB `26.4.2` result:

```text
0 rows
```

So ArcadeDB behaves as if the path does not exist.

**Control cases**

The same pattern works correctly in an ordinary `MATCH`:

```cypher
MATCH p = (:Person)-[:R1|R2*]->(:City)
RETURN size(nodes(p)) AS pathLen;
```

Observed result on Neo4j and ArcadeDB `26.4.2`:

```text
3
```

If the shortest-path call uses a single relationship type instead of an alternation, ArcadeDB also behaves correctly:

```cypher
MATCH p = allShortestPaths((:Person)-[:R1*]->(:Mid))
RETURN size(nodes(p)) AS pathLen;
```

Observed result on Neo4j and ArcadeDB `26.4.2`:

```text
2
```

If the type alternation is kept but variable length is removed, ArcadeDB again behaves correctly:

```cypher
MATCH p = allShortestPaths((:Person)-[:R1|R2]->(:Mid))
RETURN size(nodes(p)) AS pathLen;
```

Observed result on Neo4j and ArcadeDB `26.4.2`:

```text
2
```

So the bad behavior starts specifically at the combination:

- shortest-path function
- variable length
- type alternation

The same root issue also appears with `shortestPath(...)`:

```cypher
MATCH p = shortestPath((:Person)-[:R1|R2*]->(:City))
RETURN size(nodes(p)) AS pathLen;
```

Observed results:

- Neo4j: `3`
- ArcadeDB `26.4.2`: `0 rows`

So this is not limited to `allShortestPaths(...)`.

**Stronger variant from the current differential**

The same family first surfaced in a larger mixed-type query:

Setup:

```cypher
CREATE (a:Person {name: 'Alice', age: 30, active: true}),
       (b:Person {name: 'Bob', age: 25, active: false}),
       (c:City {name: 'New York', population: 8000000}),
       (d:City {name: 'London', population: 9000000}),
       (a)-[:KNOWS]->(b),
       (a)-[:LIVES_IN]->(c),
       (b)-[:LIVES_IN]->(d);
```

Query:

```cypher
MATCH (p:Person)
WHERE p.active = true
WITH p
OPTIONAL MATCH path = allShortestPaths((p)-[:KNOWS|LIVES_IN*]-(:City))
RETURN p.name AS name, size(nodes(path)) AS pathLen
ORDER BY pathLen;
```

Observed results:

- Neo4j:

```text
Alice, 2
Alice, 3
```

- ArcadeDB `26.4.2`:

```text
Alice, null
```

Direct minimization shows that `OPTIONAL MATCH` is not the root cause.
The smaller failure already appears in plain `MATCH p = allShortestPaths((:Person)-[:R1|R2*]->(:City))`.

**Why this matters**
This can silently break path-based logic that relies on mixed relationship types, including:

- shortest-path exploration across heterogeneous edge classes
- optional path pipelines
- later `nodes(path)` / `relationships(path)` projections
- count and aggregation logic built on shortest-path results

with a false `0 rows` / `null path` result instead of a matching path.

**Verification environment**
Verified directly against:

- Neo4j Docker container: `neo4j:latest`
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
  - `FalkorDB__FalkorDB__1466__q006` / `case_e41cc91e7f6d`
