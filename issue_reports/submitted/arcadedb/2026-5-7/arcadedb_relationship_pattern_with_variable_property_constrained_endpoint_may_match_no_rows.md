done
**Suggested title**
`Relationship pattern with a variable property-constrained endpoint may match no rows`

**ArcadeDB version**
Observed on Docker images:

- `arcadedata/arcadedb:latest`
- `arcadedata/arcadedb:26.3.2`
- `arcadedata/arcadedb:26.4.1-SNAPSHOT`
- `arcadedata/arcadedb:26.4.2`

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
ArcadeDB may silently match no rows when a relationship pattern ends at a property-constrained node whose property value comes from a variable.

In the minimized repro below, the graph clearly contains:

- `(:Person {name:'Alice'})-[:FRIEND]->(:Person {name:'Bob'})`

Neo4j correctly matches that edge when the endpoint is written as:

```cypher
(:Person {name: fName})
```

with `fName = 'Bob'`.

ArcadeDB accepts the query but returns `0 rows` on every tested line.

This does not look like a general property-constrained endpoint problem:

- the same pattern works if the endpoint uses a literal property value
- the same query also works if the property check is moved out of the node pattern and into a later `WHERE`

So the failure is specifically tied to using a variable inside the endpoint property map of a relationship pattern.

**To Reproduce**

Setup:

```cypher
CREATE (a:Person {name:'Alice'}),
       (b:Person {name:'Bob'}),
       (a)-[:FRIEND]->(b);
```

Query:

```cypher
WITH 'Bob' AS fName
MATCH (p:Person)-[:FRIEND]->(:Person {name: fName})
RETURN p.name AS person
ORDER BY person;
```

**Expected behavior**
Observed Neo4j result:

```text
Alice
```

ArcadeDB should return the same row.

**Actual behavior**
Observed ArcadeDB results on all tested versions:

```text
0 rows
```

So the relationship pattern behaves as if the endpoint property-constrained node does not exist.

**Control cases**

If the same endpoint constraint is expressed with a literal inside the node pattern, ArcadeDB behaves correctly:

```cypher
MATCH (p:Person)-[:FRIEND]->(:Person {name:'Bob'})
RETURN p.name AS person
ORDER BY person;
```

Observed result on Neo4j and all tested ArcadeDB versions:

```text
Alice
```

If the same value is kept in a variable but the property check is moved into `WHERE`, ArcadeDB also behaves correctly:

```cypher
WITH 'Bob' AS fName
MATCH (p:Person)-[:FRIEND]->(f:Person)
WHERE f.name = fName
RETURN p.name AS person
ORDER BY person;
```

Observed result on Neo4j and all tested ArcadeDB versions:

```text
Alice
```

So the graph itself is fine and variable equality on the endpoint is fine.
The failure starts specifically when the variable is placed inside the endpoint node pattern:

```cypher
(:Person {name: fName})
```

**Stronger carried-variable variant**

The same failure also appears when the property value is carried from an earlier match rather than introduced as a literal variable:

Setup:

```cypher
CREATE (a:Person {name:'Alice'})-[:FRIEND]->(b:Person {name:'Bob'})-[:FRIEND]->(c:Person {name:'Charlie'});
```

Query:

```cypher
MATCH (p:Person)-[:FRIEND]->(friend:Person)
WITH p, friend.name AS fName
MATCH (p)-[:FRIEND]->(:Person {name: fName})
RETURN p.name AS person, fName
ORDER BY person, fName;
```

Observed results:

- Neo4j:

```text
Alice, Bob
Bob, Charlie
```

- ArcadeDB `latest / 26.3.2 / 26.4.1-SNAPSHOT / 26.4.2`:

```text
0 rows
```

So this is not just about `WITH 'Bob' AS fName`.
Carried values from earlier matches fail in the same way.

**Original differential seed**

The family first resurfaced in the current feature-only random differential in a noisier aggregated form:

Setup:

```cypher
CREATE (a:Person {name: 'Alice'})-[:FRIEND]->(b:Person {name: 'Bob'})-[:FRIEND]->(c:Person {name: 'Charlie'});
```

Query:

```cypher
MATCH (p:Person)-[r:FRIEND]->(friend:Person)
WITH p, collect(friend.name) AS friends, count(r) AS relCount
UNWIND friends AS fName
MATCH (p)-[r2:FRIEND]->(f:Person {name: fName})
RETURN p.name, fName, r2.since;
```

Observed results:

- Neo4j:

```text
Alice, Bob, null
Bob, Charlie, null
```

- ArcadeDB `26.4.2`:

```text
0 rows
```

Direct minimization shows that `collect(...)`, `count(...)`, and `UNWIND` are not required for reproduction.
The simpler root cause is the variable-driven property-constrained endpoint pattern itself.

**Why this is kept separate**
This is related to:

- [arcadedb_merge_with_unbound_property_constrained_endpoint_may_reuse_existing_node.md](D:/sci/works/get%20bugs/graph-bug-fuzz-mvp/issue_reports/submitted/arcadedb/2026-5-1/arcadedb_merge_with_unbound_property_constrained_endpoint_may_reuse_existing_node.md)

but the boundary is different.

That report is about `MERGE` reusing an existing node instead of creating a fresh one.
This report is about `MATCH` silently returning `0 rows` when a relationship-pattern endpoint uses a variable inside its property map.

So this is kept separate.

**Verification environment**
Verified directly against:

- Neo4j Docker container: `neo4j:latest`
- ArcadeDB Docker container: `arcadedata/arcadedb:latest`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.3.2`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.4.1-SNAPSHOT`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.4.2`
- Local differential-testing harness
- Direct ArcadeDB HTTP execution using the same request mode as Studio

**Stage3 provenance**
- Stage3 run: `historical_bug_queries_parallel_llm_v1_feature_only_random_20260506`
- Stage3 artifact: `data/batch_runs/historical_bug_queries_parallel_llm_v1_feature_only_random_20260506/stage3x10/stage3_main/instantiated_cases.jsonl`
- Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_arcadedb_26_4_2_feature_only_random_20260507`
- Differential bucket: `all_passed_different_result`
- Example source query from this run:
  - `FalkorDB__FalkorDB__621__q001` / `case_cf70c7cb6d1e`
