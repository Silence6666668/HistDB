**Suggested title**
`A later UNION branch may not see nodes created in an earlier branch`

**Memgraph version**
Observed on:

- Memgraph `3.9.0`

Docker image used locally:
- `memgraph/memgraph:3.9.0`

**Discovered on**
- `2026-04-30`

**Environment**
- Host OS: Windows
- Architecture: x86_64
- Deployment: Docker
- Connection method: Bolt
- Client used for direct reproduction: repo Stage4 executor using Python `neo4j` Bolt driver
- Differential comparison target: Neo4j Community `2026.02.2`

**Describe the bug**
Memgraph may fail to expose nodes created in an earlier `UNION` branch to a later branch in the same query.

In the minimized repro below, the first branch starts from an existing `Alice` node and creates a fresh `Bob` node.
The later branch then scans `:Person` again.

Neo4j returns both names:

```text
Alice
Bob
```

Memgraph returns only:

```text
Alice
```

So the later `UNION` branch does not appear to see the `Bob` node that was created by the earlier branch.

This does not look like a generic write-visibility problem, because the same write followed by a later `MATCH` works correctly outside `UNION`.

**To Reproduce**
1. Start the Memgraph Docker container.
2. Run:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name: 'Alice'});

MATCH (n:Person)
CREATE (n)-[:KNOWS]->(:Person {name: 'Bob'})
RETURN DISTINCT n.name AS name
UNION
MATCH (n:Person)
RETURN DISTINCT n.name AS name;
```

**Expected behavior**
Observed Neo4j result:

```text
Alice
Bob
```

Memgraph should return the same two rows.

**Actual behavior**
Observed Memgraph result:

```text
Alice
```

The `Bob` node created by the first branch is not visible to the later branch.

**Control query 1**
If the same write is followed by a later `MATCH` without `UNION`, both engines behave correctly:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name: 'Alice'});

MATCH (n:Person)
CREATE (n)-[:KNOWS]->(:Person {name: 'Bob'})
WITH 1 AS _
MATCH (n:Person)
RETURN DISTINCT n.name AS name;
```

Observed result on both Neo4j and Memgraph:

```text
Alice
Bob
```

So this is not a blanket "same-query write then read" failure.

**Control query 2**
If `UNION` is kept but the first branch is read-only, both engines also behave normally:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name: 'Alice'});

MATCH (n:Person)
RETURN DISTINCT n.name AS name
UNION
MATCH (n:Person)
RETURN DISTINCT n.name AS name;
```

Observed result on both Neo4j and Memgraph:

```text
Alice
```

So `UNION` by itself is not enough.

**Control query 3**
The same visibility failure also appears with `UNION ALL`:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name: 'Alice'});

MATCH (n:Person)
CREATE (n)-[:KNOWS]->(:Person {name: 'Bob'})
RETURN DISTINCT n.name AS name
UNION ALL
MATCH (n:Person)
RETURN DISTINCT n.name AS name;
```

Observed Neo4j result:

```text
Alice
Bob
Alice
```

Observed Memgraph result:

```text
Alice
Alice
```

So the later branch still fails to see the freshly created `Bob` node even without deduplication.

**Control query 4**
If the write is moved into the later branch instead, both engines line up:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name: 'Alice'});

MATCH (n:Person)
RETURN DISTINCT n.name AS name
UNION
MATCH (n:Person)
CREATE (n)-[:KNOWS]->(:Person {name: 'Bob'})
RETURN DISTINCT n.name AS name;
```

Observed result on both Neo4j and Memgraph:

```text
Alice
```

This makes the boundary much clearer:

- a later ordinary `MATCH` branch can miss nodes created in an earlier branch
- the same write is visible outside `UNION`
- the issue is not caused by `UNION` deduplication itself, because `UNION ALL` also fails

**Additional supporting repro**
The same family also appears when the earlier branch creates a new `City` node and the later branch counts cities with the same name:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Person {name:'Alice'}), (:City {name:'Paris'});

MATCH (p:Person) WHERE p.name = 'Alice'
CREATE (p)-[:LIKES]->(:City {name:'Paris'})
RETURN count(*) AS cnt
UNION ALL
MATCH (c:City) WHERE c.name = 'Paris'
RETURN count(c) AS cnt;
```

Observed results:

- Neo4j:

```text
1
2
```

- Memgraph:

```text
1
1
```

If the same write and later `MATCH` are executed as separate queries, both engines return `2` for the city count. So the later `UNION ALL` branch still behaves as if it sees only the pre-branch node set.

**Differential seed**
This family was first noticed in:

- `neo4j__13317__q002`
- `FalkorDB__FalkorDB__126__q001` / `case_db27d2a38620`

**Verification Environment**
Plain Memgraph Docker image is enough.

Preferred verification target:
- Docker image
- x86_64
- `memgraph/memgraph`

I can verify a fix using:
- plain Memgraph Docker image on Windows host
- repo Stage4 executor / direct Bolt execution
