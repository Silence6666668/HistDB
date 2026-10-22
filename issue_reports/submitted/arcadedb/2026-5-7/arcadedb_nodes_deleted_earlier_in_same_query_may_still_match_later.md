done
**Suggested title**
`Nodes deleted earlier in the same query may still match later`

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
ArcadeDB may continue to match nodes that were deleted earlier in the same query.

In the minimized repro below, the query deletes the end node of each relationship, deletes the relationship itself, and then performs a later `MATCH (n:Node)`.

Neo4j only sees the surviving node `A`.
ArcadeDB still sees both `A` and the already-deleted node `B`.

So this is not just an orphan-check bug.
The deleted node itself remains visible to a later scan inside the same query.

**To Reproduce**

Setup:

```cypher
CREATE (a:Node {name:'A'})-[:REL]->(b:Node {name:'B'});
```

Query:

```cypher
MATCH p = ()-[*]->()
WITH relationships(p) AS rels
FOREACH (r IN rels | DELETE endNode(r) DELETE r)
MATCH (n:Node)
RETURN n.name AS name
ORDER BY name;
```

**Expected behavior**
After deleting `B` and the `REL` edge, only `A` should still exist.

Observed Neo4j result:

```text
A
```

ArcadeDB should return the same single row.

**Actual behavior**
Observed ArcadeDB result on all tested versions:

```text
A
B
```

So the node deleted through `DELETE endNode(r)` is still visible to the later `MATCH`.

**Control case**
If there is no later match, the deletion query itself completes normally:

```cypher
MATCH p = ()-[*]->()
WITH relationships(p) AS rels
FOREACH (r IN rels | DELETE endNode(r) DELETE r)
RETURN 1 AS x;
```

Observed result on Neo4j and all tested ArcadeDB versions:

```text
1
```

So the problem is not that `DELETE endNode(r)` is rejected.
The issue is that the later query stage still sees nodes that should already be gone.

**Orphan-check variant**
The same root issue also appears if the later stage only asks for isolated nodes:

```cypher
MATCH p = ()-[*]->()
WITH relationships(p) AS rels
FOREACH (r IN rels | DELETE endNode(r) DELETE r)
MATCH (n:Node)
WHERE NOT (n)--()
RETURN n.name AS orphan
ORDER BY orphan;
```

Observed results:

- Neo4j:

```text
A
```

- ArcadeDB:

```text
A
B
```

So the deleted node is not only still visible.
It is also treated as a surviving orphan.

**Stronger reproducer from the current feature-only differential**
The same family also surfaced in a slightly richer shape:

```cypher
CREATE (a:Node {name:'A'})-[:REL]->(b:Node {name:'B'})-[:REL]->(c:Node {name:'C'});

MATCH p = ()-[*]->()
WITH p, relationships(p) AS rels
FOREACH (r IN reverse(rels) | DELETE endNode(r) DELETE r)
WITH count(*) AS cnt
OPTIONAL MATCH (n:Node)
WHERE NOT (n)--()
RETURN n.name AS orphan, cnt AS deleted_count
ORDER BY orphan;
```

Observed results:

- Neo4j:

```text
A, 3
```

- ArcadeDB:

```text
A, 3
B, 3
C, 3
```

So the wrong visibility can leak through longer pipelines as well.

**Why this looks like a real bug**
The bad result is not limited to a stale returned value from the deleted node variable itself.
The deleted node can be rediscovered by a fresh later `MATCH (n:Node)`.

That means the later stage is not seeing the same graph state that the earlier delete stage should have produced.

**Differential seed**
This family surfaced while minimizing:

- `RedisGraph__RedisGraph__1268__q007`

Original generated query:

```cypher
MATCH p = ()-[*]->()
WITH p, relationships(p) AS rels
FOREACH (r IN reverse(rels) | DELETE endNode(r) DELETE r)
WITH count(*) AS cnt
OPTIONAL MATCH (n:Node)
WHERE NOT (n)--()
RETURN n.name AS orphan, cnt AS deleted_count
```

Direct reduction shows the root issue is already present in the smaller single-edge repro above.

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
  - `RedisGraph__RedisGraph__1268__q007` / `case_ec6b0b7126be`
