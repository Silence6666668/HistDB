**Suggested title**
`Variable-length path matching may ignore paths contributed by a self-loop`

**FalkorDB version**
Observed on:

- FalkorDB `4.18.1`

Docker image used locally:
- `falkordb/falkordb:latest`

**Discovered on**
- `2026-04-23`

**Environment**
- Host OS: Windows
- Architecture: x86_64
- Deployment: Docker
- Connection method: RESP / `GRAPH.QUERY`
- Client used for direct reproduction: direct `redis-cli GRAPH.QUERY`
- Differential comparison target: Neo4j Community `2026.02.2`

**Describe the bug**
FalkorDB may undercount bounded variable-length paths when one of the valid paths is contributed by a self-loop on the start node.

In the minimized repro below, there are two valid paths from `Diana` to `Bob` with length `1..2`:

1. the direct edge `Diana -> Bob`
2. the two-hop path `Diana -> Diana -> Bob` that uses the self-loop first

Neo4j counts both paths and returns `2`.
FalkorDB returns `1`, as if the self-loop-contributed path did not exist.

This looks like a path-enumeration bug rather than a general self-loop visibility issue, because the self-loop itself is still matchable.

**To Reproduce**
1. Start the FalkorDB Docker container.
2. Run:

```cypher
CREATE (d:Person {name:'Diana'}),
       (b:Person {name:'Bob'}),
       (d)-[:KNOWS]->(b),
       (d)-[:KNOWS]->(d);

MATCH path = (:Person {name:'Diana'})-[:KNOWS*1..2]->(:Person {name:'Bob'})
RETURN count(path) AS pathCount;
```

**Expected behavior**
Neo4j returns:

```text
2
```

FalkorDB should return the same count, because both the direct path and the self-loop-extended two-hop path are valid.

**Actual behavior**
FalkorDB returns:

```text
1
```

**Control query 1**
Without the self-loop, both engines return `1`:

```cypher
CREATE (d:Person {name:'Diana'}),
       (b:Person {name:'Bob'}),
       (d)-[:KNOWS]->(b);

MATCH path = (:Person {name:'Diana'})-[:KNOWS*1..2]->(:Person {name:'Bob'})
RETURN count(path) AS pathCount;
```

Observed result on both Neo4j and FalkorDB:

```text
1
```

So the direct edge itself is not the problem.

**Control query 2**
If the two-hop alternative goes through a distinct intermediate node instead of a self-loop, both engines return `2`:

```cypher
CREATE (d:Person {name:'Diana'}),
       (x:Person {name:'Xavier'}),
       (b:Person {name:'Bob'}),
       (d)-[:KNOWS]->(b),
       (d)-[:KNOWS]->(x),
       (x)-[:KNOWS]->(b);

MATCH path = (:Person {name:'Diana'})-[:KNOWS*1..2]->(:Person {name:'Bob'})
RETURN count(path) AS pathCount;
```

Observed result on both Neo4j and FalkorDB:

```text
2
```

So bounded variable-length path counting is not generally broken; the problem is tied to the self-loop-contributed path.

**Control query 3**
The self-loop itself is still visible on both engines:

```cypher
CREATE (d:Person {name:'Diana'}),
       (b:Person {name:'Bob'}),
       (d)-[:KNOWS]->(b),
       (d)-[:KNOWS]->(d);

MATCH path = (:Person {name:'Diana'})-[:KNOWS*1..2]->(:Person {name:'Diana'})
RETURN count(path) AS pathCount;
```

Observed result on both Neo4j and FalkorDB:

```text
1
```

So the bug is not that FalkorDB cannot see the self-loop at all. The issue is specifically that the self-loop-backed path to a different endpoint is dropped from the variable-length path count.

**Stronger reproducer**
The same issue also appears in a larger write-and-read query:

```cypher
CREATE (a:Person {name: 'Alice', age: 30});
CREATE (b:Person {name: 'Bob', age: 25});
CREATE (c:Person {name: 'Charlie', age: 35});
CREATE (d:Person {name: 'Diana', age: 28});

MATCH (p1:Person), (p2:Person)
WHERE p1.age > 25 AND p2.age < 30
CREATE (p1)-[:KNOWS]->(p2)
SET p1 += {status: 'active'}
WITH p1, p2
MATCH path = (p1)-[:KNOWS*1..2]->(p2)
RETURN p1.name AS p1, p2.name AS p2, count(path) AS pathCount
ORDER BY p1, p2;
```

Observed Neo4j results:

```text
Alice,   Bob,   2
Alice,   Diana, 2
Charlie, Bob,   2
Charlie, Diana, 2
Diana,   Bob,   2
Diana,   Diana, 1
```

Observed FalkorDB results:

```text
Alice,   Bob,   2
Alice,   Diana, 2
Charlie, Bob,   2
Charlie, Diana, 2
Diana,   Bob,   1
Diana,   Diana, 1
```

The only missing path is the self-loop-contributed `Diana -> Diana -> Bob` one, which matches the minimized repro.

**Additional boundary check**
This family is related to, but distinct from, the existing "fresh relationship invisible to later `MATCH`" family.

I checked the following controls on the same four-person graph:

```cypher
CREATE (:Person {name:'Alice', age:30}),
       (:Person {name:'Bob', age:25}),
       (:Person {name:'Charlie', age:35}),
       (:Person {name:'Diana', age:28});
```

If the self-loop case is removed with `AND p1 <> p2`, both engines agree:

```cypher
MATCH (p1:Person), (p2:Person)
WHERE p1.age > 25 AND p2.age < 30 AND p1 <> p2
CREATE (p1)-[:KNOWS]->(p2)
WITH p1, p2
MATCH path = (p1)-[:KNOWS*1..2]->(p2)
RETURN count(path) AS pathCount;
```

Observed result on both Neo4j and FalkorDB:

```text
7
```

So the mismatch is tightly tied to the self-loop-contributed case, not to the whole write-and-read query shape.

By contrast, the direct one-hop rematch does hit the separate read-after-write visibility family:

```cypher
MATCH (p1:Person), (p2:Person)
WHERE p1.age > 25 AND p2.age < 30
CREATE (p1)-[:KNOWS]->(p2)
WITH p1, p2
MATCH path = (p1)-[:KNOWS*1]->(p2)
RETURN count(path) AS pathCount;
```

Observed Neo4j result:

```text
6
```

Observed FalkorDB result:

```text
0
```

That one-hop failure is consistent with the existing same-query relationship-visibility bug.

However, even when the relationships are created first and the path count is run in a later statement, the self-loop-related undercount still remains:

```cypher
MATCH (p1:Person), (p2:Person)
WHERE p1.age > 25 AND p2.age < 30
CREATE (p1)-[:KNOWS]->(p2);

MATCH (p1:Person), (p2:Person)
WHERE p1.age > 25 AND p2.age < 30
MATCH path = (p1)-[:KNOWS*1..2]->(p2)
RETURN count(path) AS pathCount;
```

Observed Neo4j result:

```text
11
```

Observed FalkorDB result:

```text
10
```

So the self-loop-contributed path undercount survives even after separating the write and read phases into different statements. That makes this a real path-enumeration bug, not just another symptom of the read-after-write relationship-visibility family.

**Differential seed**
This family was first noticed in:

- `ArcadeData__arcadedb__3391__q001`

**Verification Environment**
Plain FalkorDB Docker image is enough.

Preferred verification target:
- Docker image
- x86_64
- `falkordb/falkordb`

I can verify a fix using:
- plain FalkorDB Docker image on Windows host
- direct `redis-cli GRAPH.QUERY`
