done
**Suggested title**
`UNWIND` over a pattern comprehension may fail with an internal alias-resolution error

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
FalkorDB may fail with an internal alias-resolution error when `UNWIND` is applied directly to a pattern comprehension result.

In the minimized repro below, the pattern comprehension:

```cypher
[()-[]->() | 1]
```

should evaluate to a one-element list when the graph contains one relationship.

Neo4j therefore returns one row:

```text
1
```

FalkorDB instead fails with:

```text
_AR_EXP_UpdateEntityIdx: Unable to locate a value with alias [()-[]->()|1] within the record
```

This does not look like a general pattern-comprehension problem, because FalkorDB can return the same pattern-comprehension value directly. The failure starts when the result is fed into `UNWIND`.

**To Reproduce**
1. Start the FalkorDB Docker container.
2. Run:

```cypher
CREATE (:A)-[:R]->(:B);

UNWIND [()-[]->() | 1] AS x
RETURN x;
```

**Expected behavior**
Observed Neo4j result:

```text
1
```

FalkorDB should return the same single row.

**Actual behavior**
Observed FalkorDB result:

```text
_AR_EXP_UpdateEntityIdx: Unable to locate a value with alias [()-[]->()|1] within the record
```

So the query is accepted, but execution fails while resolving the pattern-comprehension result for `UNWIND`.

**Control query 1**
Returning the same pattern comprehension directly works on both engines:

```cypher
CREATE (:A)-[:R]->(:B);

RETURN [()-[]->() | 1] AS xs;
```

Observed result on both Neo4j and FalkorDB:

```text
[1]
```

So the issue is not that FalkorDB fundamentally cannot evaluate the pattern comprehension.

**Control query 2**
`UNWIND` over a literal list also works on both engines:

```cypher
UNWIND [1] AS x
RETURN x;
```

Observed result on both Neo4j and FalkorDB:

```text
1
```

So the problem is not a generic `UNWIND` failure either.

**Control query 3**
The failure is not tied to the specific projected expression `1`. It also reproduces for other pattern-comprehension bodies:

```cypher
CREATE (:A)-[:R]->(:B);

UNWIND [()-[]->() | true] AS x
RETURN x;
```

Observed Neo4j result:

```text
true
```

Observed FalkorDB result:

```text
_AR_EXP_UpdateEntityIdx: Unable to locate a value with alias [()-[]->()|true] within the record
```

Likewise for:

```cypher
CREATE (:A)-[:R]->(:B);

UNWIND [()-[]->() | reduce(a = 0, b IN [1,2,3] | a + b)] AS x
RETURN x;
```

Observed Neo4j result:

```text
6
```

Observed FalkorDB result:

```text
_AR_EXP_UpdateEntityIdx: Unable to locate a value with alias [()-[]->()|reduce(a=0, b in [1,2,3]|a + b)] within the record
```

So the problem appears to be specifically about `UNWIND` consuming a pattern-comprehension result, not about the body expression.

**Control query 4**
The failure also reproduces when the pattern comprehension binds an explicit path variable:

```cypher
CREATE (:A)-[:R]->(:B);

UNWIND [p = ()-[]->() | 1] AS x
RETURN x;
```

Observed result:

- Neo4j: `1`
- FalkorDB: the same internal alias-resolution error

So the issue is not tied to omitting the path variable.

**Differential seed**
This family surfaced while minimizing:

- `RedisGraph__RedisGraph__3032__q001`

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
- Query ID: `RedisGraph__RedisGraph__3032__q001`
- Case ID: `case_c3f8921ce491`
