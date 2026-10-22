**Suggested title**
`Correlated EXISTS { ... } in WHERE may trigger internal error "Expected to generate all filters!"`

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
- Client used for direct reproduction: Python `neo4j` Bolt driver and `mgconsole`
- Differential comparison target: Neo4j Community `2026.02.2`

**Describe the bug**
Memgraph can raise an internal query-engine error for correlated `EXISTS { ... }` predicates that appear inside a normal `WHERE` clause, even though simpler `EXISTS { ... }` predicates in `WHERE` work.

The observed Memgraph error is:

```text
Expected to generate all filters! Please contact Memgraph support as this scenario should not happen and is very likely a bug in the query engine!
```

Neo4j accepts the same queries and returns normal results.

This looks like a planner or filter-generation bug rather than an unsupported feature, because:

- `EXISTS { ... }` in `WHERE` is already supported in simpler cases
- the failure appears only when the subquery is correlated in a more complex way
- the error text itself says the scenario should not happen

**To Reproduce**
1. Start the Memgraph Docker container.
2. Run the following query:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice', age:30}),
       (:Person {name:'Bob', age:25}),
       (:Person {name:'Charlie', age:35});
MATCH (a:Person {name:'Alice'}), (b:Person {name:'Bob'}), (c:Person {name:'Charlie'})
CREATE (a)-[:KNOWS {since:2020}]->(b),
       (a)-[:KNOWS {since:2019}]->(c);

MATCH (p:Person {age: 30})
OPTIONAL MATCH (p)-[:KNOWS]->(friend)
WHERE EXISTS {
  MATCH (p)-[:KNOWS*1..2]-(other)
  WHERE other.name = friend.name
}
RETURN p.name AS personName, friend.name AS friendName
ORDER BY friendName;
```

**Expected behavior**
Neo4j returns:

```text
Alice, Bob
Alice, Charlie
```

Memgraph should either return the same rows or reject the query with a normal semantic error if some construct is unsupported.

**Actual behavior**
Memgraph fails with:

```text
Expected to generate all filters! Please contact Memgraph support as this scenario should not happen and is very likely a bug in the query engine!
```

Rechecked on Memgraph `3.9.0` on `2026-04-23`:

- the first reproducer still fails with the same internal error
- the second reproducer still fails with the same internal error
- the simpler control query still succeeds

**Second independent reproducer**
The same internal error appears with list membership captured from an earlier `WITH`:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice', age:30}),
       (:Person {name:'Bob', age:25}),
       (:Person {name:'Charlie', age:35}),
       (:Company {name:'TechCorp'});
MATCH (a:Person {name:'Alice'}), (c:Company {name:'TechCorp'})
CREATE (a)-[:WORKS_FOR]->(c);

OPTIONAL MATCH (p:Person)
WHERE p.age > 28
WITH collect(p) AS persons
MATCH (c:Company)
WHERE EXISTS {
  MATCH (p2)
  WHERE p2 IN persons AND (p2)-[:WORKS_FOR]->(c)
}
RETURN c.name AS company, size(persons) AS totalPersons;
```

Neo4j returns:

```text
TechCorp, 2
```

Memgraph fails with the same internal error.

**Third smaller reproducer**
The failure can also be reduced to a very small `IN` predicate inside the correlated `EXISTS` body:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice', age:30}),
       (:Person {name:'Bob', age:25}),
       (:Company {name:'TechCorp'});
MATCH (a:Person {name:'Alice'}), (c:Company {name:'TechCorp'})
CREATE (a)-[:WORKS_FOR]->(c);

MATCH (c:Company)
WHERE EXISTS {
  MATCH (p:Person)
  WHERE p.age IN [30] AND (p)-[:WORKS_FOR]->(c)
}
RETURN c.name AS company;
```

Observed behavior:
- Neo4j returns:
  - `TechCorp`
- Memgraph fails with the same internal error:

```text
Expected to generate all filters! Please contact Memgraph support as this scenario should not happen and is very likely a bug in the query engine!
```

This variant is useful because it shows the trigger does not require `collect(p)` or a carried outer list variable. A simple `IN [...]` membership test combined with a correlated relationship pattern inside `EXISTS { ... }` is enough.

**Fourth supporting reproducer**
The same internal error also appears when the correlated `EXISTS` body contains an inner `OPTIONAL MATCH`:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice'}),
       (:Person {name:'Bob'}),
       (:Person {name:'Charlie'});
MATCH (a:Person {name:'Alice'}), (b:Person {name:'Bob'}), (c:Person {name:'Charlie'})
CREATE (a)-[:KNOWS]->(b), (b)-[:KNOWS]->(c);

MATCH (y:Person)
WHERE EXISTS {
  MATCH (y)-[:KNOWS]->(z:Person)
  OPTIONAL MATCH (z)-[:KNOWS]->(w:Person)
  RETURN w
}
RETURN y.name AS name
ORDER BY name;
```

Observed behavior:

- Neo4j returns:
  - `Alice`
  - `Bob`
- Memgraph fails with the same internal error:

```text
Expected to generate all filters! Please contact Memgraph support as this scenario should not happen and is very likely a bug in the query engine!
```

If the same query is rewritten without the inner `OPTIONAL MATCH`, Memgraph behaves normally:

```cypher
MATCH (y:Person)
WHERE EXISTS {
  MATCH (y)-[:KNOWS]->(:Person)-[:KNOWS]->(:Person {name:'Charlie'})
}
RETURN y.name AS name
ORDER BY name;
```

Observed result on Neo4j and Memgraph:

```text
Alice
```

So this supporting reproducer makes the boundary sharper:

- correlated `EXISTS { ... }` alone is not enough
- adding an inner `OPTIONAL MATCH` inside the correlated `EXISTS` body is enough to trigger the internal filter-generation error

**Control query**
A simpler `EXISTS { ... }` predicate in `WHERE` works normally on Memgraph:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice', age:30}),
       (:Person {name:'Bob', age:25}),
       (:Company {name:'TechCorp'});
MATCH (a:Person {name:'Alice'}), (c:Company {name:'TechCorp'})
CREATE (a)-[:WORKS_FOR]->(c);

MATCH (c:Company)
WHERE EXISTS {
  MATCH (:Person)-[:WORKS_FOR]->(c)
}
RETURN c.name AS company;
```

Observed result on both Neo4j and Memgraph:

```text
TechCorp
```

So this does not look like a blanket lack of support for `EXISTS { ... }` in `WHERE`. It looks more specific to correlated filter generation.

**Differential seeds**
This family showed up in the batch run through at least:

- `RedisGraph__RedisGraph__589__q009`
- `neo4j__13109__q002`
- `RedisGraph__RedisGraph__2095__q002`

**Verification Environment**
Plain Memgraph Docker image is enough.

Preferred verification target:
- Docker image
- x86_64
- `memgraph/memgraph`

I can verify a fix using:
- plain Memgraph Docker image on Windows host
- Bolt connection using `mgconsole` and Python Bolt driver

**Additional current-version recheck**
Rechecked on `2026-06-15` against Memgraph `5.9.0` community (`memgraph/memgraph:latest` locally) during a Neo4j `2025.05.1` vs Memgraph full historical-query comparison.

A smaller reproducer still triggers the same internal filter-generation error:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name:"A", age:30}), (:Person {name:"B", age:25});

MATCH (n:Person)
WHERE EXISTS { MATCH (m:Person) WHERE m.age > n.age }
RETURN n.name AS name
ORDER BY name;
```

Observed:

- Neo4j `2025.05.1`: returns `B`
- Memgraph `5.9.0`: fails with:

```text
Expected to generate all filters! Please contact Memgraph support as this scenario should not happen and is very likely a bug in the query engine!
```

This was not filed as a separate report because it is the same `correlated EXISTS` / filter-generation family.
