done
**Suggested title**
`Deleted node values may retain pre-delete properties when returned after DELETE`

**FalkorDB version**
Observed on:

- FalkorDB `4.18.1`
- FalkorDB `4.18.2`

Docker image used locally:
- `falkordb/falkordb:latest`
- `falkordb/falkordb:v4.18.2`

**Discovered on**
- `2026-04-24`

**Environment**
- Host OS: Windows
- Architecture: x86_64
- Deployment: Docker
- Connection method: RESP / `GRAPH.QUERY`
- Client used for direct reproduction: local Python stage4 executor over Neo4j Bolt and FalkorDB RESP
- Differential comparison target: Neo4j Community `2026.02.2`

**Describe the bug**
FalkorDB may keep deleted node values materialized with their pre-delete properties when the deleted variable is returned later in the same query.

In the minimized repro below, Neo4j deletes the matched node and then returns the deleted variable as an empty node value:

```text
{}
```

FalkorDB instead returns the deleted node with its original properties still attached:

```text
{name: 'Alice', age: 30}
```

This suggests the issue is not general `DELETE` support, but specifically the value propagated for a deleted node variable after deletion.

**To Reproduce**
1. Start the FalkorDB Docker container.
2. Run:

```cypher
CREATE (:Person {name:'Alice', age:30});

MATCH (p:Person)
DELETE p
RETURN p;
```

**Expected behavior**
Observed Neo4j result:

```text
{}
```

FalkorDB should return the same deleted-node value shape.

**Actual behavior**
Observed FalkorDB result:

```text
{name: 'Alice', age: 30}
```

The deleted node is returned with its pre-delete properties still intact.

The same minimal repro was re-checked directly on FalkorDB `4.18.2` and still returns:

```text
{name: 'Alice', age: 30}
```

**Control query 1**
The delete itself succeeds on both engines when only a scalar count is returned:

```cypher
CREATE (:Person {name:'Alice', age:30});

MATCH (p:Person)
DELETE p
RETURN count(*) AS deletedCount;
```

Observed result on both Neo4j and FalkorDB:

```text
1
```

So the problem is not that FalkorDB fails to perform the delete.

**Control query 2**
If a scalar alias is preserved through `WITH`, both engines still agree on the scalar, but only FalkorDB keeps the deleted node value hydrated:

```cypher
CREATE (:Person {name:'Alice', age:30});

MATCH (p:Person)
WITH p, p.name AS name
DELETE p
RETURN name, p;
```

Observed Neo4j result:

```text
Alice, {}
```

Observed FalkorDB result:

```text
Alice, {name: 'Alice', age: 30}
```

This narrows the issue to the deleted node value itself rather than to scalar propagation across `WITH`.

**Stronger reproducer**
The same issue also appears when deleted variables are carried across a later `WITH *` and another delete:

```cypher
CREATE (:Person {name:'Alice', age:30});
CREATE (:Person {name:'Bob', age:25});
CREATE (:Company {name:'TechCorp', industry:'Technology'});
CREATE (:Company {name:'DataInc', industry:'Technology'});

MATCH (p:Person)
WHERE p.age > 25
DELETE p
WITH *
MATCH (c:Company)
WHERE c.industry = 'Technology'
DELETE c
RETURN *;
```

Observed Neo4j result:

```text
{}, {}
{}, {}
```

Observed FalkorDB result:

```text
{name: 'Alice', age: 30}, {name: 'TechCorp', industry: 'Technology'}
{name: 'Alice', age: 30}, {name: 'DataInc', industry: 'Technology'}
```

So the problem is not limited to a single-variable `DELETE RETURN p`; deleted node values can remain fully materialized even after later query stages.

**Differential seed**
This family was first noticed in:

- `FalkorDB__FalkorDB__1482__q004`

Related supporting seeds from the same rerun:

- `RedisGraph__RedisGraph__2212__q002`
- `neo4j__12844__q007`
- `RedisGraph__RedisGraph__2420__q002`

**Additional supporting reproducer 1**
The same issue also appears when the deleted node is carried through an alias before deletion:

```cypher
CREATE (:Person {name: 'Alice O\'Connor', age: 30});

MATCH (p:Person)
WHERE p.name = 'Alice O\'Connor'
WITH p
UNWIND [p] AS unwound_p
DELETE p
SET p.age = 40
RETURN unwound_p, p
LIMIT 1;
```

Observed Neo4j result:

```text
unwound_p = {}
p = {}
```

Observed FalkorDB result:

```text
unwound_p = {name: 'Alice O''Connor', age: 30}
p = {name: 'Alice O''Connor', age: 30}
```

So the problem is not limited to the directly deleted variable. FalkorDB can also keep pre-delete properties on aliases that still reference the same deleted node value.

**Additional supporting reproducer 2**
The same stale deleted-node value also appears when the node is deleted after an `OPTIONAL MATCH` pipeline:

```cypher
CREATE (:Person {name:'Alice', age:30});

MATCH (p:Person {name: 'Alice'})
OPTIONAL MATCH (p)-[r:WORKS_AT]->(c:Company)
WITH p, r, c, COUNT(c) AS companyCount
DELETE p
RETURN p AS deletedPerson,
       r AS deletedRelationship,
       c AS deletedCompany,
       companyCount;
```

Observed Neo4j result:

```text
deletedPerson = {}
deletedRelationship = null
deletedCompany = null
companyCount = 0
```

Observed FalkorDB result:

```text
deletedPerson = {name: 'Alice', age: 30}
deletedRelationship = null
deletedCompany = null
companyCount = 0
```

So the family is not limited to a direct `MATCH ... DELETE ... RETURN p`. The stale deleted-node value also survives through a preceding `OPTIONAL MATCH` and aggregation step.

**Additional supporting reproducer 3**
The same stale deleted-node value also survives when the node is first updated and then deleted through a later re-match in the same query:

```cypher
CREATE (:Person {name:'Alice', age:30});
CREATE (:Person {name:'Bob', age:25});
CREATE (:Person {name:'Charlie', age:35});
CREATE (:Person {name:'Diana', age:28});

MATCH (p:Person)
WHERE p.age > 25
SET p.status = 'active'
WITH p
MATCH (p2:Person)
WHERE p2.name = p.name
DELETE p2
RETURN p, count(p) AS total_active
ORDER BY total_active DESC;
```

Observed Neo4j result:

```text
{}, 1
{}, 1
{}, 1
```

Observed FalkorDB result:

```text
{name: 'Alice', age: 30, status: 'active'}, 1
{name: 'Charlie', age: 35, status: 'active'}, 1
{name: 'Diana', age: 28, status: 'active'}, 1
```

So the stale deleted-node value is not limited to a plain delete. It also survives an earlier `SET` and a later delete performed through a second bound variable in the same query.

**Related note**
This looks distinct from:

- [falkordb_delete_may_report_zero_and_leave_path_visible.md](D:/sci/works/get%20bugs/graph-bug-fuzz-mvp/issue_reports/submitted/falkordb/old/falkordb_delete_may_report_zero_and_leave_path_visible.md)

That submitted family is about delete side effects, counts, and later path visibility. The current family is about the returned value of an already deleted node variable.

**Verification Environment**
Plain FalkorDB Docker image is enough.

Preferred verification target:
- Docker image
- x86_64
- `falkordb/falkordb`

I can verify a fix using:
- plain FalkorDB Docker image on Windows host
- local Python stage4 executor over RESP

**Stage3 provenance**
This report predates explicit file-level Stage3 provenance tracking.

Later current-version supporting case:

- Stage3 run: `historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b`
- Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_falkordb_4_18_2_skeleton_20260501`
- Query ID: `neo4j__13596__q001`
- Case ID: `case_290d8e4f9ebb`

Additional supporting cases from the same run:

- `neo4j__13596__q003` / `case_f80d3742b125`
- `neo4j__13596__q004` / `case_33ba1120801e`
