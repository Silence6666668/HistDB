done
**Suggested title**
`ALL list predicate on a carried variable after WITH may filter out matching rows`

**FalkorDB version**
Observed on:

- FalkorDB `4.18.2`
- FalkorDB `4.18.4`

Docker images used locally:
- `falkordb/falkordb:v4.18.2`
- `falkordb/falkordb:v4.18.4`

**Discovered on**
- `2026-05-01`

**Environment**
- Host OS: Windows
- Architecture: x86_64
- Deployment: Docker
- Connection method: RESP / `GRAPH.QUERY`
- Client used for direct reproduction: repo executor + direct RESP execution
- Differential comparison target: Neo4j Community

**Describe the bug**
FalkorDB may incorrectly filter out a row when an `all(...)` list predicate is applied to a variable carried through `WITH`.

In the minimized repro below, the only `:Person` node has:

```text
tags = ['friend', 'colleague']
```

So the predicate:

```cypher
all(item IN ['friend', 'colleague'] WHERE item IN a.tags)
```

should be true.

Neo4j returns the row.
FalkorDB returns `0 rows`.

This does not look like a generic `all(...)` evaluation problem, because the same predicate works on both engines when evaluated directly in `RETURN`, and it also works on both engines when used directly in `MATCH ... WHERE ... RETURN ...` without the intermediate `WITH`.

**To Reproduce**
1. Start the FalkorDB Docker container.
2. Run:

```cypher
CREATE (n:Person {name: 'Alice', tags: ['friend', 'colleague']});

MATCH (a:Person)
WITH a
WHERE all(item IN ['friend', 'colleague'] WHERE item IN a.tags)
RETURN a.name AS name, a.tags AS tags;
```

**Expected behavior**
Observed Neo4j result:

```text
Alice, ['friend', 'colleague']
```

FalkorDB should return the same row.

**Actual behavior**
Observed FalkorDB result:

```text
0 rows
```

**Control query 1**
If the same predicate is evaluated directly, both engines agree that it is true:

```cypher
CREATE (n:Person {name: 'Alice', tags: ['friend', 'colleague']});

MATCH (a:Person)
RETURN all(item IN ['friend', 'colleague'] WHERE item IN a.tags) AS ok;
```

Observed result on both Neo4j and FalkorDB:

```text
true
```

So the `all(...)` expression itself can be evaluated correctly.

**Control query 2**
If the same predicate is used directly in `MATCH ... WHERE ... RETURN ...`, both engines also return the row:

```cypher
CREATE (n:Person {name: 'Alice', tags: ['friend', 'colleague']});

MATCH (a:Person)
WHERE all(item IN ['friend', 'colleague'] WHERE item IN a.tags)
RETURN a.name AS name, a.tags AS tags;
```

Observed result on both Neo4j and FalkorDB:

```text
Alice, ['friend', 'colleague']
```

So the issue is not a generic failure of `WHERE all(...)`.

**Control query 3**
If the row is carried through `WITH` but the filter is rewritten without `all(...)`, both engines behave correctly:

```cypher
CREATE (n:Person {name: 'Alice', tags: ['friend', 'colleague']});

MATCH (a:Person)
WITH a
WHERE 'friend' IN a.tags AND 'colleague' IN a.tags
RETURN a.name AS name, a.tags AS tags;
```

Observed result on both Neo4j and FalkorDB:

```text
Alice, ['friend', 'colleague']
```

So the problem is not simply that list-property filtering after `WITH` is broken.

**Control query 4**
Renaming the carried variable does not avoid the issue:

```cypher
CREATE (n:Person {name: 'Alice', tags: ['friend', 'colleague']});

MATCH (a:Person)
WITH a AS p
WHERE all(item IN ['friend', 'colleague'] WHERE item IN p.tags)
RETURN p.name AS name, p.tags AS tags;
```

Observed result:

- Neo4j: `Alice, ['friend', 'colleague']`
- FalkorDB: `0 rows`

So the failure is not tied to reusing the same variable name across the `WITH`.

**Differential seed**
This family surfaced while minimizing:

- `RedisGraph__RedisGraph__2853__q010`

**Verification Environment**
Plain FalkorDB Docker image is enough.

Preferred verification target:
- Docker image
- x86_64
- `falkordb/falkordb:v4.18.2`

I can verify a fix using:
- plain FalkorDB Docker image on Windows host
- project FalkorDB executor / direct RESP execution

**Stage3 provenance**
- Stage3 run: `historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b`
- Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_falkordb_4_18_2_skeleton_20260501`
- Query ID: `RedisGraph__RedisGraph__2853__q010`
- Case ID: `case_253f6d13d04f`
