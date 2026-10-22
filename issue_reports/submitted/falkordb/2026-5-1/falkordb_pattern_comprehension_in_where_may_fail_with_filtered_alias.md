done
**Suggested title**
`Pattern comprehension in WHERE may fail with "Unable to resolve filtered alias"`

**FalkorDB version**
Observed on:

- FalkorDB `4.18.1`
- FalkorDB `4.18.2`
- FalkorDB `4.18.4`

Docker image used locally:
- `falkordb/falkordb:latest`
- `falkordb/falkordb:v4.18.2`
- `falkordb/falkordb:v4.18.4`

**Discovered on**
- `2026-04-24`

**Environment**
- Host OS: Windows
- Architecture: x86_64
- Deployment: Docker
- Connection method: RESP / `GRAPH.QUERY`
- Client used for direct reproduction: direct `redis-cli GRAPH.QUERY`
- Differential comparison target: Neo4j Community `2026.02.2`

**Describe the bug**
FalkorDB may fail when a `WHERE` predicate uses `ANY(...)` over a pattern comprehension that refers to already bound variables.

In the minimized repro below, the graph contains one obvious:

```cypher
(:Person {name:'Alice'})-[:FRIEND_OF {tags:['friend']}]->(:Person {name:'Bob'})
```

Neo4j correctly keeps the row for:

```cypher
WHERE ANY(rel IN [(n)-[r:FRIEND_OF]->(m) | r] WHERE 'friend' IN rel.tags)
```

FalkorDB instead fails with:

```text
Unable to resolve filtered alias '[(n)-[r:FRIEND_OF]->(m) | r]'
```

This looks distinct from general traversal support, because the same pattern is readable through a normal `MATCH`, and the same `ANY(...)` predicate works once the relationship list is materialized first with `collect(r)`.

**To Reproduce**
1. Start the FalkorDB Docker container.
2. Run:

```cypher
CREATE (:Person {name:'Alice'})-[:FRIEND_OF {tags:['friend']}]->(:Person {name:'Bob'});

MATCH (n:Person {name:'Alice'}), (m:Person {name:'Bob'})
WHERE ANY(rel IN [(n)-[r:FRIEND_OF]->(m) | r] WHERE 'friend' IN rel.tags)
RETURN n.name AS person1, m.name AS person2;
```

**Expected behavior**
Neo4j returns:

```text
Alice, Bob
```

FalkorDB should return the same row.

**Actual behavior**
FalkorDB fails with:

```text
Unable to resolve filtered alias '[(n)-[r:FRIEND_OF]->(m) | r]'
```

**Control query 1**
The same pattern is directly traversable on both engines:

```cypher
CREATE (:Person {name:'Alice'})-[:FRIEND_OF {tags:['friend']}]->(:Person {name:'Bob'});

MATCH (n:Person {name:'Alice'})-[r:FRIEND_OF]->(m:Person {name:'Bob'})
RETURN n.name AS person1, m.name AS person2;
```

Observed result on both Neo4j and FalkorDB:

```text
Alice, Bob
```

So the graph state and basic traversal are fine.

**Control query 2**
The same pattern comprehension works in `RETURN` on both engines:

```cypher
CREATE (:Person {name:'Alice'})-[:FRIEND_OF {tags:['friend']}]->(:Person {name:'Bob'});

MATCH (n:Person {name:'Alice'}), (m:Person {name:'Bob'})
RETURN [(n)-[r:FRIEND_OF]->(m) | type(r)] AS rel_types;
```

Observed result on both Neo4j and FalkorDB:

```text
["FRIEND_OF"]
```

So the issue is not that FalkorDB rejects pattern comprehension in general. The failure is specific to using the pattern comprehension as part of a `WHERE` filter.

**Control query 3**
The same `ANY(...)` logic also works on both engines if the relationship list is materialized first:

```cypher
CREATE (:Person {name:'Alice'})-[:FRIEND_OF {tags:['friend']}]->(:Person {name:'Bob'});

MATCH (n:Person {name:'Alice'})-[r:FRIEND_OF]->(m:Person {name:'Bob'})
WITH n, m, collect(r) AS rels
WHERE ANY(rel IN rels WHERE 'friend' IN rel.tags)
RETURN n.name AS person1, m.name AS person2;
```

Observed result on both Neo4j and FalkorDB:

```text
Alice, Bob
```

This makes the boundary clearer: the issue is not `ANY(...)` itself and not relationship-tag filtering in general. The failure is tied to the `WHERE` predicate consuming a pattern-comprehension expression over bound aliases.

**Differential seed**
This family was first noticed in:

- `FalkorDB__FalkorDB__1548__q005`

**Related note**
This looks related to, but distinct from:

- [falkordb_pattern_exists_in_where_may_fail_with_filtered_alias.md](D:/sci/works/get%20bugs/graph-bug-fuzz-mvp/issue_reports/submitted/falkordb/2026-5-1/falkordb_pattern_exists_in_where_may_fail_with_filtered_alias.md)

That family covers `WHERE EXISTS((p)-[:REL]->(...))`. The current family covers `WHERE ANY(... IN [(pattern) | ...] ...)`.

**Verification Environment**
Plain FalkorDB Docker image is enough.

Preferred verification target:
- Docker image
- x86_64
- `falkordb/falkordb`

I can verify a fix using:
- plain FalkorDB Docker image on Windows host
- direct `redis-cli GRAPH.QUERY`

**Stage3 provenance**
This report predates explicit file-level Stage3 provenance tracking.

Later current-version supporting cases were also observed in:

- Stage3 run: `historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b`
- Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_falkordb_4_18_2_skeleton_20260501`
- Query ID: `FalkorDB__FalkorDB__310__q002`
- Case ID: `case_7b2679b1f2d6`

Additional supporting `4.18.2` case from the same run:

- `RedisGraph__RedisGraph__2623__q002` / `case_6a5685ac89bd`
