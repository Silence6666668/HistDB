done
**Suggested title**
`CALL db.labels() YIELD label` may nullify carried variables

**ArcadeDB version**
Observed on Docker images:

- `arcadedata/arcadedb:26.4.1-SNAPSHOT`
- `arcadedata/arcadedb:26.4.2`

Not reproduced on older local images because the procedure is not available there:

- `arcadedata/arcadedb:latest`
- `arcadedata/arcadedb:26.3.2`

On those older lines, the same queries fail with:

```text
Procedure not found: db.labels
```

The `26.4.x` line is different: `db.labels()` is accepted and returns labels, but previously bound variables are replaced with `null`.

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
On ArcadeDB `26.4.1-SNAPSHOT` and `26.4.2`, `CALL db.labels() YIELD label` may nullify variables that were already bound earlier in the same query.

In the minimized repro below, the query starts with a simple carried scalar:

```cypher
WITH 1 AS x
CALL db.labels() YIELD label
RETURN x, label;
```

Neo4j keeps `x = 1` for every row.
ArcadeDB returns label rows, but `x` becomes `null`.

This is not just about graph-bound variables.
The same failure also affects carried node variables and path endpoints.

**To Reproduce**

Query:

```cypher
WITH 1 AS x
CALL db.labels() YIELD label
RETURN x, label
ORDER BY label
LIMIT 5;
```

**Expected behavior**
Neo4j returns rows like:

```text
1, Airport
1, Book
1, City
...
```

ArcadeDB should preserve the carried scalar `x` in the same way.

**Actual behavior**
Observed ArcadeDB `26.4.1-SNAPSHOT` / `26.4.2` result:

```text
null, Animal
null, ChainPerson
null, Checked
...
```

So the procedure call yields rows, but the previously bound variable is lost.

**Control cases**

The procedure itself works and returns labels:

```cypher
CALL db.labels() YIELD label
RETURN label
ORDER BY label
LIMIT 5;
```

Observed result on both Neo4j and ArcadeDB `26.4.1-SNAPSHOT` / `26.4.2`:

```text
<label rows>
```

So the problem is not that `db.labels()` fails to execute.

A plain graph match without the procedure also preserves bound values correctly:

```cypher
CREATE (a:Person {name:'Alice'})-[:FRIEND]->(b:Person {name:'Bob'});

MATCH (n:Person)-[:FRIEND]->(m:Person)
RETURN n.name AS n_name, m.name AS m_name;
```

Observed result on both Neo4j and ArcadeDB:

```text
Alice, Bob
```

So the earlier bound variables themselves are valid.

The failure starts when the carried variables cross the `CALL db.labels() YIELD label` boundary:

```cypher
MATCH (n:Person)-[:FRIEND]->(m:Person)
CALL db.labels() YIELD label
RETURN n.name AS n_name, m.name AS m_name, label
ORDER BY label
LIMIT 5;
```

Observed results:

- Neo4j:

```text
Alice, Bob, Airport
Alice, Bob, Book
Alice, Bob, City
...
```

- ArcadeDB `26.4.1-SNAPSHOT` / `26.4.2`:

```text
null, null, Address/Animal/...
```

And the same thing still happens if the variables are materialized through `WITH` before the call:

```cypher
MATCH (n:Person)-[:FRIEND]->(m:Person)
WITH n, m
CALL db.labels() YIELD label
RETURN n.name AS n_name, m.name AS m_name, label
ORDER BY label
LIMIT 5;
```

Observed ArcadeDB result:

```text
null, null, <label>
```

So this is not a matter of a missing `WITH`.
The carried variables are already in scope, but `db.labels()` still wipes them out.

**Stronger variant from the current skeleton differential**

The same family also reproduces after an `OPTIONAL MATCH`, where the carried variables are then used by a later aggregate:

```cypher
MATCH (p:Person)
OPTIONAL MATCH (p)-[:LIVES_IN]->(city:City)
WHERE city.name = 'NYC'
WITH p, city
CALL db.labels() YIELD label
WITH p, city, label
RETURN count(p) AS cnt;
```

Observed results:

- Neo4j: `26`
- ArcadeDB `26.4.1-SNAPSHOT` / `26.4.2`: `0`

So the damage is not limited to returning corrupted scalar or node values.
Once `CALL db.labels()` nullifies the carried variables, later aggregates can collapse as well.

**Why this matters**
This is not a formatting difference and not just a missing-procedure issue on the current line.
ArcadeDB accepts the procedure call, returns rows, and silently corrupts the carried state by replacing bound values with `null`.
That can break later filtering, joins, writes, and aggregations in any query shape that combines a procedure call with previously bound variables.

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
- Stage3 run: `historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b`
- Query ID: `FalkorDB__FalkorDB__138__q001`
- Case ID: `case_f57dc7b7634f`
- Stage3 artifact: `data/batch_runs/historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b/stage3_main/instantiated_cases.jsonl`
- Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_arcadedb_skeleton_20260430`
- Differential bucket: `all_passed_different_result`

Additional current-version skeleton-run seed:

- Stage3 run: `historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b`
- Query ID: `FalkorDB__FalkorDB__305__q004`
- Case ID: `case_10d9ee85b025`
- Stage3 artifact: `data/batch_runs/historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b/stage3_main/instantiated_cases.jsonl`
- Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_arcadedb_skeleton_20260430`
- Differential bucket: `all_passed_different_result`
