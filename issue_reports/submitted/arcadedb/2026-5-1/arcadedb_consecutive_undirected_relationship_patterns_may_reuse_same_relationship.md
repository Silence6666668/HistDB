done
**Suggested title**
`Consecutive undirected relationship patterns may reuse the same relationship`

**ArcadeDB version**
Observed on Docker images:

- `arcadedata/arcadedb:26.4.1-SNAPSHOT`
- `arcadedata/arcadedb:26.4.2`

Not reproduced on:

- `arcadedata/arcadedb:latest`
- `arcadedata/arcadedb:26.3.2`

The current local moving `latest` container behaves differently from the explicit `26.4.1-SNAPSHOT` image line here.

**Discovered on**
- `2026-04-24`

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
ArcadeDB may reuse the same relationship twice inside a single multi-hop undirected pattern.

In the minimized repro below, the graph is just:

```text
Alice -[:KNOWS]-> Bob -[:KNOWS]-> Charlie
```

For the pattern:

```cypher
(a)-[r1:KNOWS]-(b)-[r2:KNOWS]-(c)
```

Neo4j only returns the two valid traversals that use the two distinct `KNOWS` relationships.
ArcadeDB additionally returns rows where `r1 = r2`, such as `Alice, Bob, Alice` and `Charlie, Bob, Charlie`.

That means the same relationship is being reused on both sides of the middle node within one matched path shape.

**To Reproduce**

Setup:

```cypher
CREATE (a:Person {name:'Alice'}),
       (b:Person {name:'Bob'}),
       (c:Person {name:'Charlie'}),
       (a)-[:KNOWS]->(b),
       (b)-[:KNOWS]->(c);
```

Query:

```cypher
MATCH (a:Person)-[r1:KNOWS]-(b:Person)-[r2:KNOWS]-(c:Person)
RETURN a.name AS a, b.name AS b, c.name AS c, r1 = r2 AS sameRel
ORDER BY a, b, c;
```

**Expected behavior**
Only the two traversals that use distinct relationships should be returned:

```text
Alice,   Bob, Charlie, false
Charlie, Bob, Alice,   false
```

Observed Neo4j result:

```text
Alice,   Bob, Charlie, false
Charlie, Bob, Alice,   false
```

**Actual behavior**
ArcadeDB returns six rows:

```text
Alice,   Bob, Alice,   true
Alice,   Bob, Charlie, false
Bob,     Alice, Bob,   true
Bob,     Charlie, Bob, true
Charlie, Bob, Alice,   false
Charlie, Bob, Charlie, true
```

So ArcadeDB is allowing the same `KNOWS` relationship to satisfy both `r1` and `r2`.

**Control cases**

If distinctness is enforced explicitly, ArcadeDB behaves correctly:

```cypher
MATCH (a:Person)-[r1:KNOWS]-(b:Person)-[r2:KNOWS]-(c:Person)
WHERE r1 <> r2
RETURN a.name AS a, b.name AS b, c.name AS c
ORDER BY a, b, c;
```

Observed result on Neo4j and all tested ArcadeDB versions:

```text
Alice,   Bob, Charlie
Charlie, Bob, Alice
```

The directed form is also fine:

```cypher
MATCH (a:Person)-[:KNOWS]->(b:Person)-[:KNOWS]->(c:Person)
RETURN a.name AS a, b.name AS b, c.name AS c
ORDER BY a, b, c;
```

Observed result on Neo4j and all tested ArcadeDB versions:

```text
Alice, Bob, Charlie
```

This makes the boundary fairly clear: the problem is tied to consecutive undirected relationship patterns, where ArcadeDB may reuse one edge multiple times in the same match.

**Stronger reproducer**
The same issue also corrupts downstream aggregation, because the duplicated undirected matches get counted as if they were distinct traversals.

Setup:

```cypher
CREATE (:ChainPerson {name:'Alice', age:30});
CREATE (:ChainPerson {name:'Bob', age:25});
CREATE (:ChainPerson {name:'Charlie', age:35});

MATCH (a:ChainPerson {name:'Alice'}), (b:ChainPerson {name:'Bob'})
CREATE (a)-[:KNOWS]->(b);

MATCH (b:ChainPerson {name:'Bob'}), (c:ChainPerson {name:'Charlie'})
CREATE (b)-[:KNOWS]->(c);
```

Query:

```cypher
MATCH (a:ChainPerson)-[:KNOWS]-(b:ChainPerson)-[:KNOWS]-(c:ChainPerson)
WHERE a.age > 25
WITH a.name AS person_name, COUNT(b) AS connection_count
WHERE connection_count > 0
RETURN person_name, connection_count
ORDER BY person_name;
```

Observed Neo4j result:

```text
Alice,   1
Charlie, 1
```

Observed ArcadeDB results:

- `latest`: same as Neo4j
- `26.3.2`: same as Neo4j
- `26.4.1-SNAPSHOT`:

```text
Alice,   2
Charlie, 2
```

- `26.4.2`:

```text
Alice,   2
Charlie, 2
```

So the relationship-reuse bug is not just a row-shape problem.
It also doubles aggregates built on top of the duplicated matches.

**Differential seed**
This family was first noticed in the latest Neo4j-vs-ArcadeDB differential run from:

- `FalkorDB__FalkorDB__309__q012`

**Verification environment**
Verified directly against:

- Neo4j Docker container: `neo4j:latest`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.3.2`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.4.1-SNAPSHOT`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.4.2`
- ArcadeDB Docker container: current moving `arcadedata/arcadedb:latest`
- Local differential-testing harness
- Direct ArcadeDB HTTP execution using the same request mode as Studio

**Stage3 provenance**
This report predates explicit file-level Stage3 provenance tracking.
The underlying differential-seed source query was instantiated in:

- Stage3 run: `historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b`
- Query ID: `FalkorDB__FalkorDB__309__q012`
- Case ID: `case_2f777341a8a3`
- Stage3 artifact: `data/batch_runs/historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b/stage3_main/instantiated_cases.jsonl`
