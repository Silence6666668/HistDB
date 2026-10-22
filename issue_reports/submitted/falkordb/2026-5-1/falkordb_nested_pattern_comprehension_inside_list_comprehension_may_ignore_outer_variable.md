done
**Suggested title**
`Nested pattern comprehension inside a list comprehension may ignore the outer variable`

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
FalkorDB may ignore the current outer-list element when a pattern comprehension is nested inside a list comprehension.

In the minimized repro below, the outer list is:

```cypher
[a, b]
```

where:

- `a` is `Alice`
- `b` is `Bob`

and each node has exactly one outgoing `[:KNOWS]` edge:

- `Alice -> Bob`
- `Bob -> Charlie`
- `Charlie -> Alice`

Neo4j returns:

```text
[['Bob'], ['Charlie']]
```

because the inner pattern comprehension is evaluated separately for each outer element.

FalkorDB instead returns:

```text
[['Bob', 'Charlie', 'Alice'], ['Bob', 'Charlie', 'Alice']]
```

So the inner pattern comprehension is no longer correlated with the current outer variable `n`; it behaves as if it were evaluated globally and then duplicated for each outer slot.

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
RETURN [n IN [a, b] | [(n)-[:KNOWS]->(t) | t.name]] AS x;
```

**Expected behavior**
Observed Neo4j result:

```text
[['Bob'], ['Charlie']]
```

FalkorDB should return the same nested list.

**Actual behavior**
Observed FalkorDB result:

```text
[['Bob', 'Charlie', 'Alice'], ['Bob', 'Charlie', 'Alice']]
```

The inner pattern comprehension ignores which node is currently bound to `n`.

**Control query 1**
The outer list comprehension itself works when the body does not contain a nested pattern comprehension:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Person {name: 'Alice'}),
       (b:Person {name: 'Bob'}),
       (c:Person {name: 'Charlie'}),
       (a)-[:KNOWS]->(b),
       (b)-[:KNOWS]->(c),
       (c)-[:KNOWS]->(a);

MATCH (a:Person {name:'Alice'}), (b:Person {name:'Bob'})
RETURN [n IN [a, b] | n.name] AS x;
```

Observed result on both Neo4j and FalkorDB:

```text
['Alice', 'Bob']
```

So the problem is not a generic failure of outer list comprehensions.

**Control query 2**
If the outer list is unnested into rows first, both engines behave correctly:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Person {name: 'Alice'}),
       (b:Person {name: 'Bob'}),
       (c:Person {name: 'Charlie'}),
       (a)-[:KNOWS]->(b),
       (b)-[:KNOWS]->(c),
       (c)-[:KNOWS]->(a);

MATCH (a:Person {name:'Alice'}), (b:Person {name:'Bob'})
UNWIND [a, b] AS n
RETURN n.name AS name,
       [(n)-[:KNOWS]->(t) | t.name] AS x
ORDER BY name;
```

Observed result on both Neo4j and FalkorDB:

```text
Alice, ['Bob']
Bob,   ['Charlie']
```

So the inner pattern comprehension itself can work when `n` is carried as a row variable.

**Control query 3**
The issue is not tied to returning the inner list itself. The same wrong correlation appears when only its size is returned:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Person {name: 'Alice'}),
       (b:Person {name: 'Bob'}),
       (c:Person {name: 'Charlie'}),
       (a)-[:KNOWS]->(b),
       (b)-[:KNOWS]->(c),
       (c)-[:KNOWS]->(a);

MATCH (a:Person {name:'Alice'}), (b:Person {name:'Bob'})
RETURN [n IN [a, b] | size([(n)-[:KNOWS]->(t) | t.name])] AS x;
```

Observed results:

- Neo4j: `[1, 1]`
- FalkorDB `4.18.2`: `[3, 3]`

So the problem is not only a serialization issue in the returned nested list. The matched set itself is already wrong.

**Stronger reproducer**
The same family also appears in the original path-based differential query:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Person {name: 'Alice', age: 30}),
       (b:Person {name: 'Bob', age: 25}),
       (c:Person {name: 'Charlie', age: 35}),
       (a)-[:KNOWS]->(b),
       (b)-[:KNOWS]->(c),
       (c)-[:KNOWS]->(a);

MATCH p = (n1:Person {name: 'Alice'}) -[*..2]- (n2)
RETURN [n IN nodes(p) | [(n)-[:KNOWS]->(t) | t.name]] AS x;
```

Observed Neo4j results:

```text
[['Bob'], ['Alice']]
[['Bob'], ['Alice'], ['Charlie']]
[['Bob'], ['Charlie']]
[['Bob'], ['Charlie'], ['Alice']]
```

Observed FalkorDB `4.18.2` results:

```text
[['Bob', 'Charlie', 'Alice'], ['Bob', 'Charlie', 'Alice']]
[['Bob', 'Charlie', 'Alice'], ['Bob', 'Charlie', 'Alice'], ['Bob', 'Charlie', 'Alice']]
[['Bob', 'Charlie', 'Alice'], ['Bob', 'Charlie', 'Alice']]
[['Bob', 'Charlie', 'Alice'], ['Bob', 'Charlie', 'Alice'], ['Bob', 'Charlie', 'Alice']]
```

So the loss of correlation is not specific to the fixed outer list `[a, b]`; it also appears when the outer list is derived from `nodes(p)`.

**Why this looks like a real bug**
The boundary is narrow:

- outer list comprehensions without nested pattern comprehensions work
- the same inner pattern comprehension works when `n` is carried as a row variable via `UNWIND`
- the same wrong correlation appears both in direct nested-list output and in `size(...)`

That makes this look like a specific execution bug in FalkorDB's handling of pattern comprehensions nested inside list comprehensions, not a general unsupported-feature difference.

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
