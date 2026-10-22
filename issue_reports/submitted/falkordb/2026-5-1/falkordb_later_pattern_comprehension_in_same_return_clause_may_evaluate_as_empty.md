done
**Suggested title**
`A later pattern comprehension in the same RETURN clause may evaluate as empty`

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
FalkorDB may incorrectly evaluate a later pattern comprehension in the same `RETURN` clause as an empty list, even though the same pattern comprehension works by itself.

In the minimized repro below:

- `Alice` has one outgoing `[:KNOWS]` edge to `Bob`
- `Bob` has one outgoing `[:KNOWS]` edge to `Charlie`

Neo4j returns:

```text
['Bob'], ['Charlie']
```

FalkorDB returns:

```text
['Bob'], []
```

So the first pattern comprehension is evaluated correctly, but the second one in the same `RETURN` clause is silently emptied.

**To Reproduce**
1. Start the FalkorDB Docker container.
2. Run:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Person {name: 'Alice'}),
       (b:Person {name: 'Bob'}),
       (c:Person {name: 'Charlie'}),
       (a)-[:KNOWS]->(b),
       (b)-[:KNOWS]->(c),
       (c)-[:KNOWS]->(a);

MATCH (a:Person {name:'Alice'}), (b:Person {name:'Bob'})
RETURN [(a)-[:KNOWS]->(t) | t.name] AS ax,
       [(b)-[:KNOWS]->(t) | t.name] AS bx;
```

**Expected behavior**
Observed Neo4j result:

```text
['Bob'], ['Charlie']
```

FalkorDB should return the same pair of lists.

**Actual behavior**
Observed FalkorDB result:

```text
['Bob'], []
```

The later pattern comprehension loses the matched row.

**Control query 1**
Each pattern comprehension works correctly when evaluated alone:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Person {name: 'Alice'}),
       (b:Person {name: 'Bob'}),
       (c:Person {name: 'Charlie'}),
       (a)-[:KNOWS]->(b),
       (b)-[:KNOWS]->(c),
       (c)-[:KNOWS]->(a);

MATCH (a:Person {name:'Alice'})
RETURN [(a)-[:KNOWS]->(t) | t.name] AS ax;
```

Observed result on both Neo4j and FalkorDB:

```text
['Bob']
```

Likewise for:

```cypher
MATCH (b:Person {name:'Bob'})
RETURN [(b)-[:KNOWS]->(t) | t.name] AS bx;
```

Observed result on both engines:

```text
['Charlie']
```

So the issue is not that FalkorDB fundamentally cannot evaluate either pattern comprehension.

**Control query 2**
The failure is order-sensitive. If the two pattern comprehensions are swapped, the one that appears later is still the one that becomes empty:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Person {name: 'Alice'}),
       (b:Person {name: 'Bob'}),
       (c:Person {name: 'Charlie'}),
       (a)-[:KNOWS]->(b),
       (b)-[:KNOWS]->(c),
       (c)-[:KNOWS]->(a);

MATCH (a:Person {name:'Alice'}), (b:Person {name:'Bob'})
RETURN [(b)-[:KNOWS]->(t) | t.name] AS bx,
       [(a)-[:KNOWS]->(t) | t.name] AS ax;
```

Observed results:

- Neo4j: `['Charlie'], ['Bob']`
- FalkorDB `4.18.2`: `['Charlie'], []`

So the problem is not tied to `b` specifically. It is the later pattern comprehension in the same projection that is lost.

**Control query 3**
The issue is not a generic problem with multiple projected expressions. If the second projection is a scalar, both engines behave correctly:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Person {name: 'Alice'}),
       (b:Person {name: 'Bob'}),
       (c:Person {name: 'Charlie'}),
       (a)-[:KNOWS]->(b),
       (b)-[:KNOWS]->(c),
       (c)-[:KNOWS]->(a);

MATCH (a:Person {name:'Alice'}), (b:Person {name:'Bob'})
RETURN [(a)-[:KNOWS]->(t) | t.name] AS ax,
       b.name AS bname;
```

Observed result on both Neo4j and FalkorDB:

```text
['Bob'], 'Bob'
```

So the issue is specific to multiple pattern comprehensions in the same `RETURN`, not to multiple return items in general.

**Why this looks like a real bug**
The boundary is narrow:

- either pattern comprehension works by itself
- a scalar second return item works
- whichever pattern comprehension appears later becomes empty

That makes this look like a specific execution bug in FalkorDB's handling of multiple pattern-comprehension projections, not a general unsupported-feature difference.

**Differential seed**
This family surfaced while minimizing:

- `RedisGraph__RedisGraph__2773__q001`

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
- Query ID: `RedisGraph__RedisGraph__2773__q001`
- Case ID: `case_e5f492d2652b`
