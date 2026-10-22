done
**Suggested title**
Re-matching multiple carried variables with `WHERE` may fail with `Unable to resolve filtered alias`

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
FalkorDB may fail when multiple variables are carried through `WITH` and then re-matched in a later `MATCH` that filters those same carried aliases in `WHERE`.

In the minimized repro below, Neo4j correctly returns the single expected row.

FalkorDB instead fails with:

```text
Unable to resolve filtered alias 'b'
```

This does not appear to be a general problem with:

- `WITH` in general
- `MATCH` in general
- or filtering a single carried variable in `WHERE`

The boundary appears to be specifically: re-matching multiple carried variables together and then filtering them in the later `WHERE`.

**To Reproduce**
1. Start the FalkorDB Docker container.
2. Run:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Person {name:'Alice', age:30}), (b:Person {name:'Bob', age:25})
WITH a, b
MATCH (a), (b)
WHERE a.age > 25 AND b.age < 30
RETURN a.name AS a, b.name AS b;
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
Unable to resolve filtered alias 'b'
```

**Control query 1**
If only one carried variable is re-matched and filtered, FalkorDB behaves normally:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Person {name:'Alice', age:30})
WITH a
MATCH (a)
WHERE a.age > 25
RETURN a.name AS a;
```

Observed result on both Neo4j and FalkorDB:

```text
Alice
```

So this is not a general problem with re-matching a carried variable under `WHERE`.

**Control query 2**
The same family also appears with three carried variables, where FalkorDB reports the last filtered alias instead:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Person {name:'Alice', age:30}), (b:Person {name:'Bob', age:25}), (c:Person {name:'Charlie', age:35})
WITH a, b, c
MATCH (a), (b), (c)
WHERE a.age > 25 AND b.age < 30 AND c.age > 30
RETURN a.name AS a, b.name AS b, c.name AS c;
```

Observed Neo4j result:

```text
Alice, Bob, Charlie
```

Observed FalkorDB error:

```text
Unable to resolve filtered alias 'c'
```

This suggests the issue scales with the number of carried variables and is tied to later filtered alias resolution, not to one specific variable name.

**Related behavior**
A closely related shape without the later `WHERE` is already covered by a separate crash family: re-matching multiple carried variables together can also terminate FalkorDB with exit code `139`.

This report focuses on the deterministic non-crashing variant where FalkorDB instead rejects the filtered alias.

**Differential seed**
This family was first noticed in:

- `RedisGraph__RedisGraph__1447__q002`

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

Later current-version supporting case:

- Stage3 run: `historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b`
- Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_falkordb_4_18_2_skeleton_20260501`
- Query ID: `FalkorDB__FalkorDB__1300__q001`
- Case ID: `case_bc161cde659d`

Related alias-resolution case from the same run:

- `FalkorDB__FalkorDB__1301__q001` / `case_6f88ad1212bb`
