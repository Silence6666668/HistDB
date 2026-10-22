done
**Suggested title**
Repeated relationship variable in a post-`WITH` pattern predicate may crash the server

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
- Differential comparison target: Neo4j Community `2026.02.2`

**Describe the bug**
FalkorDB may crash when a relationship variable is first carried through `WITH` and then reused inside a later pattern predicate where the same relationship variable appears multiple times.

In the minimized repro below, Neo4j treats:

```cypher
(p)-[r:KNOWS]->()<-[r:KNOWS]-()
```

as a repeated-relationship-variable pattern that can never match, so it simply returns `0 rows`.

FalkorDB instead closes the connection and the container exits with code `139`.

This does not look like a generic repeated-variable parse problem, because FalkorDB does reject the same repeated-relationship pattern gracefully when it appears directly in the original `MATCH ... WHERE ...` clause without the intermediate `WITH`.

**To Reproduce**
1. Start the FalkorDB Docker container.
2. Run:

```cypher
CREATE (:Person {name:'Alice'})-[:KNOWS]->(:Person {name:'Bob'});

MATCH (p:Person)-[r:KNOWS]-()
WITH p, r
WHERE (p)-[r:KNOWS]->()<-[r:KNOWS]-()
RETURN p.name;
```

**Expected behavior**
Observed Neo4j result:

```text
0 rows
```

FalkorDB should either return the same empty result or reject the query with a normal semantic error.

**Actual behavior**
Observed FalkorDB result:

```text
FalkorDB connection closed unexpectedly.
```

After the repro, the Docker container exits with:

```text
exited 139
```

So this is a server crash, not a normal query error.

**Control query 1**
Without the intermediate `WITH`, FalkorDB rejects the same repeated-relationship-variable pattern gracefully instead of crashing:

```cypher
CREATE (:Person {name:'Alice'})-[:KNOWS]->(:Person {name:'Bob'});

MATCH (p:Person)-[r:KNOWS]-()
WHERE (p)-[r:KNOWS]->()<-[r:KNOWS]-()
RETURN p;
```

Observed behavior:

- Neo4j returns `0 rows`
- FalkorDB reports:

```text
Cannot use the same relationship variable 'r' for multiple patterns.
```

This makes the boundary much clearer: the crash is tied to reusing the relationship variable after it has been carried through `WITH`.

**Control query 2**
The carried relationship variable works normally in non-repeated pattern predicates:

```cypher
CREATE (:Person {name:'Alice'})-[:KNOWS]->(:Person {name:'Bob'});

MATCH (p:Person)-[r:KNOWS]-()
WITH p, r
WHERE (p)-[r:KNOWS]->()
RETURN p.name;
```

Observed result on both Neo4j and FalkorDB:

```text
Alice
```

Likewise for:

```cypher
MATCH (p:Person)-[r:KNOWS]-()
WITH p, r
WHERE ()<-[r:KNOWS]-(p)
RETURN p.name;
```

Observed result on both Neo4j and FalkorDB:

```text
Alice
```

So the issue is not just that a carried relationship variable appears again in a later pattern predicate.

**Control query 3**
The same crash already appears without the extra outer `MATCH ... WITH * ...` scaffolding from the original differential seed:

```cypher
CREATE (:Person {name:'Alice'})-[:KNOWS]->(:Person {name:'Bob'});

MATCH (p:Person)-[r:KNOWS]-()
WITH *
WHERE (p)-[r:KNOWS]->()<-[r:KNOWS]-()
RETURN p;
```

Observed behavior:

- Neo4j returns `0 rows`
- FalkorDB closes the connection and the container exits with code `139`

So the minimal crash trigger is already present in the smaller one-clause form.

**Differential seed**
This family surfaced while minimizing:

- `FalkorDB__FalkorDB__1333__q001`

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
- Query ID: `FalkorDB__FalkorDB__1333__q001`
- Case ID: `case_669bf0f1b0df`
