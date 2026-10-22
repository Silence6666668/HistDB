**Suggested title**
`Pattern comprehension may ignore a relationship created earlier in the same clause`

**Memgraph version**
Observed on:

- Memgraph `3.9.0`

Docker image used locally:
- `memgraph/memgraph:3.9.0`

**Discovered on**
- `2026-05-12`

**Environment**
- Host OS: Windows
- Architecture: x86_64
- Deployment: Docker
- Connection method: Bolt
- Client used for direct reproduction: Python `neo4j` Bolt driver via the local differential-testing harness
- Differential comparison target: Neo4j Community `2026.04.0`

**Describe the bug**
Memgraph may fail to expose a relationship to a pattern comprehension when that relationship was created earlier in the same clause.

In the minimized repro below, the query:

1. matches an existing `Person` and an existing `City`
2. creates a `WORKS_IN` relationship between them
3. immediately returns a pattern comprehension over that new relationship

Neo4j returns the expected city name.
Memgraph returns an empty list.

This does not look like a generic write-visibility problem, because a nearby control that inserts a `WITH p` pipeline boundary before the `RETURN` makes Memgraph behave correctly.

**To Reproduce**
1. Start the Memgraph Docker container.
2. Run:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Charlie'}), (:City {name:'New York'});

MATCH (p:Person {name:'Charlie'}), (c:City {name:'New York'})
CREATE (p)-[:WORKS_IN]->(c)
RETURN p.name AS name,
       [ (p)-[:WORKS_IN]->(city) | city.name ] AS cities;
```

**Expected behavior**
Observed Neo4j result:

```text
Charlie, ["New York"]
```

Memgraph should return the same row.

**Actual behavior**
Observed Memgraph result:

```text
Charlie, []
```

So the relationship is created, but the immediately following pattern comprehension does not see it.

**Control query 1**
If the query returns the matched endpoint directly instead of using a pattern comprehension, both engines behave correctly:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Charlie'}), (:City {name:'New York'});

MATCH (p:Person {name:'Charlie'}), (c:City {name:'New York'})
CREATE (p)-[:WORKS_IN]->(c)
RETURN p.name AS name, c.name AS city;
```

Observed result on Neo4j and Memgraph:

```text
Charlie, New York
```

So the failure is not simply that the `CREATE` did not happen.

**Control query 2**
If the same query inserts a `WITH p` boundary after the relationship creation, Memgraph behaves correctly:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Charlie'}), (:City {name:'New York'});

MATCH (p:Person {name:'Charlie'}), (c:City {name:'New York'})
CREATE (p)-[:WORKS_IN]->(c)
WITH p
RETURN p.name AS name,
       [ (p)-[:WORKS_IN]->(city) | city.name ] AS cities;
```

Observed result on Neo4j and Memgraph:

```text
Charlie, ["New York"]
```

This makes the boundary much sharper:

- the relationship creation itself succeeds
- the same pattern comprehension works after a pipeline boundary
- the failure is specifically that the pattern comprehension cannot see the newly created relationship immediately in the same clause

**Control query 3**
This is not limited to `CREATE`. `MERGE` shows the same behavior:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Charlie'}), (:City {name:'New York'});

MATCH (p:Person {name:'Charlie'}), (c:City {name:'New York'})
MERGE (p)-[:WORKS_IN]->(c)
RETURN p.name AS name,
       [ (p)-[:WORKS_IN]->(city) | city.name ] AS cities;
```

Observed behavior:

- Neo4j returns:
  - `Charlie, ["New York"]`
- Memgraph returns:
  - `Charlie, []`

So the problem does not look specific to `CREATE` versus `MERGE`.

**Control query 4**
This is also not a blanket failure of pattern comprehensions after writes. If the entire path is created in a single `CREATE` pattern, both engines behave correctly:

```cypher
MATCH (n) DETACH DELETE n;

CREATE (p:Person {name:'Charlie'})-[:WORKS_IN]->(:City {name:'New York'})
RETURN p.name AS name,
       [ (p)-[:WORKS_IN]->(city) | city.name ] AS cities;
```

Observed result on Neo4j and Memgraph:

```text
Charlie, ["New York"]
```

So the boundary seems to be more specific: the failure appears when the relationship is created between already matched endpoints and then immediately consumed by a pattern comprehension in the same clause.

**Differential seed**
This family surfaced while minimizing:

- `FalkorDB__FalkorDB__751__q002`
- `case_8abb96f30071`

Original generated query:

```cypher
UNWIND [{name: 'Charlie', age: 30}, {name: 'Diana', age: 25}] AS person
MERGE (p:Person:Employee {name: person.name})
ON CREATE SET p.age = person.age
WITH p
MATCH (c:City {name: 'New York'})
MERGE (p)-[r:WORKS_IN {since: 2020}]->(c)
RETURN p.name AS name, p.age AS age,
       [ (p)-[:WORKS_IN]->(city) | city.name ] AS cities
```

Observed differential result:

- Neo4j returned:
  - `Charlie, 30, ["New York"]`
  - `Diana, 25, ["New York"]`
- Memgraph returned:
  - `Charlie, 30, []`
  - `Diana, 25, []`

That larger case reduces to the same root issue above.

**Verification Environment**
Plain Memgraph Docker image is enough.

Preferred verification target:
- Docker image
- x86_64
- `memgraph/memgraph`

I can verify a fix using:
- plain Memgraph Docker image on Windows host
- Bolt connection using Python `neo4j` driver
