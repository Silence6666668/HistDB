**Suggested title**
`List comprehension over nullable relationship properties after OPTIONAL MATCH filters out rows`

**Memgraph version**
Observed on:

- Memgraph `3.8.1`
- Memgraph `3.9.0`

Docker images used locally:
- `memgraph/memgraph:3.8.1-relwithdebinfo`
- `memgraph/memgraph:3.9.0`

**Discovered on**
- `2026-04-23`

**Environment**
- Host OS: Windows
- Architecture: x86_64
- Deployment: Docker
- Connection method: Bolt
- Client used for direct reproduction: Python `neo4j` Bolt driver
- Differential comparison target: Neo4j Community `2026.02.2`

**Describe the bug**
Memgraph may incorrectly filter out rows when a `WHERE` predicate evaluates a list comprehension over properties of an optionally matched relationship that can be `null`.

In the minimized repro below, Neo4j keeps all rows and evaluates:

```cypher
[x IN [r.since, r.strength] WHERE x > 5]
```

to a non-null list (typically `[]`) when `r` is `null`.

Memgraph instead returns no rows at all.

This suggests Memgraph is propagating the nullable relationship access through the list-comprehension predicate differently from Neo4j, in a way that removes rows that should survive.

**To Reproduce**
1. Start the Memgraph Docker container.
2. Run the following query:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice', age:30});
CREATE (:Person {name:'Bob', age:25});
CREATE (:Person {name:'Charlie', age:35});

MATCH (p:Person)
OPTIONAL MATCH (p)-[r:FRIEND]->()
WHERE p.age > 25
WITH p, r
WHERE [x IN [r.since, r.strength] WHERE x > 5] IS NOT NULL
RETURN p.name AS name
ORDER BY name;
```

**Expected behavior**
Neo4j returns all rows:

```text
Alice
Bob
Charlie
```

Memgraph should return the same rows.

**Actual behavior**
Memgraph returns:

```text
0 rows
```

**Control query 1**
If the list comprehension is removed, the nullable `OPTIONAL MATCH` row shape works on both engines:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice', age:30});
CREATE (:Person {name:'Bob', age:25});
CREATE (:Person {name:'Charlie', age:35});

MATCH (p:Person)
OPTIONAL MATCH (p)-[r:FRIEND]->()
WHERE p.age > 25
WITH p, r
WHERE r IS NULL
RETURN p.name AS name
ORDER BY name;
```

Observed result on both Neo4j and Memgraph:

```text
Alice
Bob
Charlie
```

**Control query 2**
If the nullable relationship properties are stabilized with `coalesce`, Memgraph also behaves normally:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice', age:30});
CREATE (:Person {name:'Bob', age:25});
CREATE (:Person {name:'Charlie', age:35});

MATCH (p:Person)
OPTIONAL MATCH (p)-[r:FRIEND]->()
WHERE p.age > 25
WITH p, r
WHERE [x IN [coalesce(r.since, -1), coalesce(r.strength, -1)] WHERE x > 5] IS NOT NULL
RETURN p.name AS name
ORDER BY name;
```

Observed result on both Neo4j and Memgraph:

```text
Alice
Bob
Charlie
```

This makes the boundary much clearer: the problem is tied to direct property access on a nullable optional relationship inside the list comprehension.

**Stronger reproducer**
The same issue also appears when one row has a real matching relationship and another survives only through the `OPTIONAL MATCH` null-extension:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Person {name:'Alice', age:30});
CREATE (b:Person {name:'Bob', age:25});
CREATE (a)-[:FRIEND {since:2020, strength:8}]->(b);

MATCH (p:Person)
OPTIONAL MATCH (p)-[r:FRIEND]->()
WHERE p.age > 25
WITH p, r
WHERE [x IN [r.since, r.strength] WHERE x > 5] IS NOT NULL
RETURN p.name AS name
ORDER BY name;
```

Observed Neo4j result:

```text
Alice
Bob
```

Observed Memgraph result:

```text
0 rows
```

**Differential seed**
This family was first noticed in:

- `RedisGraph__RedisGraph__835__q002`

**Verification Environment**
Plain Memgraph Docker image is enough.

Preferred verification target:
- Docker image
- x86_64
- `memgraph/memgraph`

I can verify a fix using:
- plain Memgraph Docker image on Windows host
- Bolt connection using Python `neo4j` driver
