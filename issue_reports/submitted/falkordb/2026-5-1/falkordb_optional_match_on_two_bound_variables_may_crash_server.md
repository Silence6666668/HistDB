done
**Suggested title**
`OPTIONAL MATCH` on two already-bound variables may crash the server

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
FalkorDB can crash when a query performs `OPTIONAL MATCH` over two variables that were already bound earlier in the pipeline.

In the minimized repro below, `a` and `b` are both already bound by the first `MATCH`.

Neo4j treats:

```cypher
OPTIONAL MATCH (a), (b)
```

as a trivial one-row optional expansion and returns:

```text
cnt = 1
```

FalkorDB instead closes the connection and the container exits with code `139`.

This does not look like a generic `OPTIONAL MATCH` problem, because `OPTIONAL MATCH (a)` and `OPTIONAL MATCH (b)` both work on FalkorDB. The crash appears when the same optional clause reintroduces two already-bound variables together.

**To Reproduce**
1. Start the FalkorDB Docker container.
2. Run:

```cypher
CREATE (:Person {name:'Alice'}), (:Person {name:'Bob'});

MATCH (a:Person {name:'Alice'}), (b:Person {name:'Bob'})
OPTIONAL MATCH (a), (b)
RETURN count(*) AS cnt;
```

**Expected behavior**
Observed Neo4j result:

```text
cnt = 1
```

FalkorDB should return the same row and remain healthy.

**Actual behavior**
On FalkorDB:

- the connection is closed unexpectedly
- the container exits
- `docker inspect` reports exit code `139`

Observed client-side error:

```text
FalkorDB connection closed unexpectedly.
```

**Control query 1**
If only one of the already-bound variables is reintroduced, both engines behave normally:

```cypher
CREATE (:Person {name:'Alice'}), (:Person {name:'Bob'});

MATCH (a:Person {name:'Alice'}), (b:Person {name:'Bob'})
OPTIONAL MATCH (a)
RETURN count(*) AS cnt;
```

Observed result on both Neo4j and FalkorDB:

```text
cnt = 1
```

Likewise for:

```cypher
CREATE (:Person {name:'Alice'}), (:Person {name:'Bob'});

MATCH (a:Person {name:'Alice'}), (b:Person {name:'Bob'})
OPTIONAL MATCH (b)
RETURN count(*) AS cnt;
```

Observed result on both engines:

```text
cnt = 1
```

So the crash is not caused by `OPTIONAL MATCH` on a bound variable in general.

**Control query 2**
The failure is not tied to aggregation. Returning the bound names directly still crashes FalkorDB:

```cypher
CREATE (:Person {name:'Alice'}), (:Person {name:'Bob'});

MATCH (a:Person {name:'Alice'}), (b:Person {name:'Bob'})
OPTIONAL MATCH (a), (b)
RETURN a.name AS aname, b.name AS bname;
```

Observed results:

- Neo4j:

```text
Alice, Bob
```

- FalkorDB:
  - connection closed unexpectedly
  - container exits with code `139`

So this is not a `count(*)`-specific issue.

**Control query 3**
Carrying the variables through `WITH` does not avoid the crash:

```cypher
CREATE (:Person {name:'Alice'}), (:Person {name:'Bob'});

MATCH (a:Person {name:'Alice'}), (b:Person {name:'Bob'})
WITH a, b
OPTIONAL MATCH (a), (b)
RETURN count(*) AS cnt;
```

Observed results:

- Neo4j: `cnt = 1`
- FalkorDB: connection closed unexpectedly, container exits with code `139`

So the issue is specifically tied to the later optional clause re-matching two carried variables together.

**Differential seed**
This family surfaced while minimizing:

- `FalkorDB__FalkorDB__1281__q002`

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
- Query ID: `FalkorDB__FalkorDB__1281__q002`
- Case ID: `case_141536e97b92`
