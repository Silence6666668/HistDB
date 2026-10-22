done
**Suggested title**
`Relationship MERGE after an earlier batched CREATE may drop all later rows`

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
- Client used for direct reproduction: direct `GRAPH.QUERY`
- Differential comparison target: Neo4j Community `2026.02.2`

**Describe the bug**
FalkorDB may drop all rows from the later part of a query when:

- an earlier phase performs a batched `CREATE`
- that earlier phase is collapsed through `WITH collect(...)`
- a later phase matches existing rows and performs a relationship `MERGE` to an inline node pattern
- the query then carries the bound values through another `WITH` and returns them

In the minimized repro below, Neo4j returns two rows for `Alice` and `Bob`.
FalkorDB returns `0 rows`.

The earlier collected list is not used later, so this does not look like a semantic data dependency issue. It looks more like the earlier batched write leaves FalkorDB in a bad pipeline state for the later `MERGE` branch.

**To Reproduce**
1. Start the FalkorDB Docker container.
2. Run setup:

```cypher
CREATE (:Person {name:'Alice', age:30}),
       (:Person {name:'Bob', age:25}),
       (:Person {name:'Charlie', age:35});
```

3. Run:

```cypher
UNWIND range(1, 5) AS i
CREATE (n:Person {id: i})
WITH collect(n) AS batchNodes
MATCH (p:Person)
WHERE p.name IN ['Alice', 'Bob']
MERGE (p)-[:KNOWS]->(new:Person {name:'NewFriend'})
WITH p, new
RETURN p.name AS personName, new.name AS newName;
```

**Expected behavior**
Neo4j returns:

```text
Alice, NewFriend
Bob, NewFriend
```

FalkorDB should return the same two rows.

**Actual behavior**
FalkorDB returns:

```text
0 rows
```

**Control query 1**
If the earlier batched `CREATE` phase is removed, both engines return the two rows:

```cypher
MATCH (p:Person)
WHERE p.name IN ['Alice', 'Bob']
MERGE (p)-[:KNOWS]->(new:Person {name:'NewFriend'})
WITH p, new
RETURN p.name AS personName, new.name AS newName;
```

Observed result on both Neo4j and FalkorDB:

```text
Alice, NewFriend
Bob, NewFriend
```

So the later `MERGE` is not broken in general.

**Control query 2**
If the later relationship write uses `CREATE` instead of `MERGE`, both engines also return the two rows:

```cypher
UNWIND range(1, 5) AS i
CREATE (n:Person {id: i})
WITH collect(n) AS batchNodes
MATCH (p:Person)
WHERE p.name IN ['Alice', 'Bob']
CREATE (p)-[:KNOWS]->(new:Person {name:'NewFriend'})
WITH p, new
RETURN p.name AS personName, new.name AS newName;
```

Observed result on both Neo4j and FalkorDB:

```text
Alice, NewFriend
Bob, NewFriend
```

So the failure is not caused by the earlier batched `CREATE` alone.

**Control query 3**
If the earlier batched `CREATE` is kept but the later phase is read-only, both engines also behave normally:

```cypher
UNWIND range(1, 5) AS i
CREATE (n:Person {id: i})
WITH collect(n) AS batchNodes
MATCH (p:Person)
WHERE p.name IN ['Alice', 'Bob']
RETURN p.name AS personName;
```

Observed result on both Neo4j and FalkorDB:

```text
Alice
Bob
```

This makes the boundary clearer: the bad behavior is tied to the combination of:

- an earlier batched `CREATE`
- a later relationship `MERGE` to an inline node pattern
- carrying the bound values through a later `WITH`

**Stronger reproducer**
The original differential seed also reproduces the same problem:

```cypher
UNWIND range(1, 5) AS i
CREATE (n:Person {id: i})
WITH collect(n) AS batchNodes
MATCH (p:Person)
WHERE any(name IN ['Alice', 'Bob'] WHERE p.name = name)
MERGE (p)-[:KNOWS]->(new:Person {name:'NewFriend'})
WITH p, new
ORDER BY CASE WHEN p.age > 30 THEN 1 ELSE 2 END
RETURN p, new;
```

Observed Neo4j result:

```text
2 rows
```

Observed FalkorDB result:

```text
0 rows
```

So the issue is not limited to a scalar projection. Returning the bound node values directly also loses all rows on FalkorDB.

**Differential seed**
This family was first noticed in:

- `RedisGraph__RedisGraph__1036__q002`

**Verification Environment**
Plain FalkorDB Docker image is enough.

Preferred verification target:
- Docker image
- x86_64
- `falkordb/falkordb`

I can verify a fix using:
- plain FalkorDB Docker image on Windows host
- direct `GRAPH.QUERY`
