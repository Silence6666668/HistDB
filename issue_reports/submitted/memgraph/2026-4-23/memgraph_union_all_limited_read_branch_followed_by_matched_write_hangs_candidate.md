**Suggested title**
`UNION ALL with a limited read branch followed by a matched write branch hangs`

**Memgraph version**
Observed on:

- Memgraph `3.9.0`

Docker image used locally:
- `memgraph/memgraph:3.9.0`

**Discovered on**
- `2026-04-23`

**Environment**
- Host OS: Windows
- Architecture: x86_64
- Deployment: Docker
- Connection method: Bolt / `mgconsole`
- Client used for direct reproduction: `mgconsole` inside Docker container
- Differential comparison target: Neo4j Community `2026.02.2`

**Describe the bug**
A `UNION ALL` query can hang on Memgraph when:

- the first branch is a read branch with `LIMIT`
- the later branch performs a write based on rows matched inside that branch

Neo4j returns promptly on the same query.

In local direct reproduction on Memgraph `3.9.0`, the minimized query below did not return within 30 seconds. In automated differential execution, the same family shows up as:

```text
Hard timeout exceeded after 25.0 seconds
```

After aborting the hanging query, subsequent queries can also fail with:

```text
Cannot resolve conflicting transactions. Retry this transaction when the conflicting transaction is finished.
```

which suggests the hung query can leave the session in a bad transactional state until the container is restarted.

**To Reproduce**
1. Start the Memgraph Docker container.
2. Run the following query:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice'}), (:Person {name:'Bob'}), (:Person {name:'Charlie'});

MATCH (p:Person {name:'Alice'}) RETURN p.name AS name LIMIT 1
UNION ALL
MATCH (p:Person)
CREATE (p)-[:FRIEND]->(:Person {name:'David'})
RETURN p.name AS name;
```

**Expected behavior**
The query should return quickly.

Observed Neo4j result:

```text
Alice
Alice
Bob
Charlie
```

Memgraph should also return promptly.

**Actual behavior**
Memgraph `3.9.0` does not return within 30 seconds in direct reproduction.

In the differential harness, related seeds fail with:

```text
Hard timeout exceeded after 25.0 seconds
```

**Control query 1**
If the first-branch `LIMIT` is removed, Memgraph returns promptly:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice'}), (:Person {name:'Bob'}), (:Person {name:'Charlie'});

MATCH (p:Person {name:'Alice'}) RETURN p.name AS name
UNION ALL
MATCH (p:Person)
CREATE (p)-[:FRIEND]->(:Person {name:'David'})
RETURN p.name AS name;
```

Observed Memgraph result:

```text
Alice
David
David
David
```

So `UNION ALL` plus the matched write branch is not enough by itself.

**Control query 2**
If the later branch is made read-only, Memgraph also returns promptly:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice'}), (:Person {name:'Bob'}), (:Person {name:'Charlie'});

MATCH (p:Person {name:'Alice'}) RETURN p.name AS name LIMIT 1
UNION ALL
MATCH (p:Person)
RETURN p.name AS name;
```

Observed Memgraph result:

```text
Alice
Alice
Bob
Charlie
```

So a limited first branch is not enough by itself either.

**Control query 3**
If the later branch performs only a standalone `CREATE`, Memgraph still returns promptly:

```cypher
MATCH (n) DETACH DELETE n;
RETURN 1 AS x LIMIT 1
UNION ALL
CREATE (:T {v: 2})
RETURN 2 AS x;
```

Observed Memgraph result:

```text
1
2
```

This makes the boundary sharper: the hang appears tied to the combination of:

- `UNION ALL`
- a limited read branch first
- followed by a later branch that writes through rows matched inside that branch

**Differential seed**
This family was first noticed in:

- `RedisGraph__RedisGraph__2667__q002`

Related timeout-style seeds from the same rerun include:

- `FalkorDB__FalkorDB__699__q005`
- `neo4j__13429__q001`

but the minimized reproducer above is the cleanest current standalone case.

**Verification Environment**
Plain Memgraph Docker image is enough.

Preferred verification target:
- Docker image
- x86_64
- `memgraph/memgraph`

I can verify a fix using:
- plain Memgraph Docker image on Windows host
- `mgconsole` inside Docker
