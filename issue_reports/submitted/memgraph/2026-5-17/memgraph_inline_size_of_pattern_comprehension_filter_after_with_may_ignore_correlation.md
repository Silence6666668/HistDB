**Suggested title**
`Inline size(pattern-comprehension) filter after WITH may ignore correlation and admit unrelated rows`

**Memgraph version**
Observed on:

- Memgraph `3.9.0`
- Memgraph `3.10.1` (re-verified on `2026-05-19`)

Docker images used locally:
- `memgraph/memgraph:3.9.0`
- `memgraph/memgraph:latest` (resolved locally to `3.10.1` on `2026-05-19`)

**Discovered on**
- `2026-05-17`

**Environment**
- Host OS: Windows
- Architecture: x86_64
- Deployment: Docker
- Connection method: Bolt
- Client used for direct reproduction: Python `neo4j` Bolt driver
- Differential comparison target: Neo4j Community `2026.04.0`

**Describe the bug**
Memgraph may evaluate a correlated filter incorrectly when:

- an earlier `MATCH` already bound `p`
- `p` is carried through a `WITH`
- the later `WHERE` uses an inline `size([...]) > 0` check over a correlated pattern-comprehension-like list expression

In the minimized repro below, Neo4j returns only `Bob`, because only `Bob` is adjacent to a `Person` whose name starts with `A`.

Memgraph instead returns:

```text
Alice
Bob
Charlie
```

This suggests the inline filter is being evaluated as if the correlation on `p` had been lost, even though the same `size(...)` expression computes the correct numeric values on both engines when returned directly.

**To Reproduce**
1. Start the Memgraph Docker container.
2. Run:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice'})-[:KNOWS]->(:Person {name:'Bob'})-[:KNOWS]->(:Person {name:'Charlie'});

MATCH (p:Person)-[:KNOWS]-(q:Person)
WITH p
WHERE size([(p)-[:KNOWS]-(r:Person) WHERE r.name STARTS WITH 'A' | r]) > 0
RETURN DISTINCT p.name AS p
ORDER BY p;
```

**Expected behavior**
Observed Neo4j result:

```text
Bob
```

Memgraph should return the same row.

**Actual behavior**
Observed Memgraph result:

```text
Alice
Bob
Charlie
```

So rows that should fail the correlated filter are still kept.

**Control query 1**
If the same correlated filter is used directly on `MATCH (p:Person)` without the earlier `MATCH ... WITH p` pipeline, both engines behave correctly:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice'})-[:KNOWS]->(:Person {name:'Bob'})-[:KNOWS]->(:Person {name:'Charlie'});

MATCH (p:Person)
WHERE size([(p)-[:KNOWS]-(r:Person) WHERE r.name STARTS WITH 'A' | r]) > 0
RETURN p.name AS p
ORDER BY p;
```

Observed result on Neo4j and Memgraph:

```text
Bob
```

So the inline `size(...) > 0` expression is not broken in complete generality.

**Control query 2**
If the `size(...)` value is first materialized in `WITH` and only then filtered, both engines also behave correctly:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice'})-[:KNOWS]->(:Person {name:'Bob'})-[:KNOWS]->(:Person {name:'Charlie'});

MATCH (p:Person)-[:KNOWS]-(q:Person)
WITH p, size([(p)-[:KNOWS]-(r:Person) WHERE r.name STARTS WITH 'A' | r]) AS c
WHERE c > 0
RETURN DISTINCT p.name AS p
ORDER BY p;
```

Observed result on Neo4j and Memgraph:

```text
Bob
```

This makes the boundary much sharper: the problem is tied to the **inline** filter form, not to the `size(...)` computation itself.

**Control query 3**
If the list itself is first materialized and then tested with `hits <> []`, both engines also line up:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice'})-[:KNOWS]->(:Person {name:'Bob'})-[:KNOWS]->(:Person {name:'Charlie'});

MATCH (p:Person)-[:KNOWS]-(q:Person)
WITH p, [(p)-[:KNOWS]-(r:Person) WHERE r.name STARTS WITH 'A' | r] AS hits
WHERE hits <> []
RETURN DISTINCT p.name AS p
ORDER BY p;
```

Observed result on Neo4j and Memgraph:

```text
Bob
```

So Memgraph can handle the correlated pattern expression once the list result is stabilized before filtering.

**Stronger reproducer**
The larger differential case that first exposed this family was:

```cypher
MATCH (p:Person)-[:KNOWS*1..2]-(q:Person)
WITH p, q
WHERE size([(p)-[:KNOWS]-(r:Person) WHERE r.name STARTS WITH 'A' | r]) > 0
WITH p, collect(q) AS friends
MATCH (p)-[:KNOWS]-(friend:Person)
WITH p, friend, friends
WHERE friend IN friends
WITH p, collect(friend.name) AS friendNames
UNWIND (CASE WHEN friendNames = [] THEN [null] ELSE friendNames END) AS name
FOREACH (ignore IN CASE WHEN name IS NULL THEN [] ELSE [1] END |
  CREATE (n:Node {label: name}) SET n.created = true)
WITH p, collect(name) AS names
CREATE (m:Node {label: p.name}) SET m.origin = true
RETURN m;
```

Observed result:

- Neo4j returned one row for `Bob`
- Memgraph returned rows for `Alice`, `Bob`, `Charlie`, `Diana`, and `Eve`

That larger divergence reduces to the same root issue above: the inline correlated filter admits rows that should not survive.

And the same larger query family also realigns on both engines if the inline filter is rewritten into either of these equivalent forms before the later writes:

```cypher
WITH p, q, size([(p)-[:KNOWS]-(r:Person) WHERE r.name STARTS WITH 'A' | r]) AS c
WHERE c > 0
```

or

```cypher
WITH p, q, [(p)-[:KNOWS]-(r:Person) WHERE r.name STARTS WITH 'A' | r] AS hits
WHERE hits <> []
```

In both control forms, Neo4j and Memgraph return only `Bob`. So the write-heavy case does not indicate a separate `CREATE`-specific bug; it is consistent with the same inline-correlation-loss issue.

**Additional supporting reproducer 2**
The same family also appears in a `UNION ALL` query that uses an outgoing correlated pattern expression after `WITH`:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Person {name: 'Alice', age: 30, city: 'NYC'})-[:KNOWS {since: 2020}]->(b:Person {name: 'Bob', age: 25, city: 'LA'});
CREATE (b)-[:FRIENDS {since: 2019}]->(c:Person {name: 'Charlie', age: 35, city: 'SF'});
CREATE (a)-[:WORKS_AT {since: 2018}]->(:Company {name: 'Acme', location: 'NYC'});
CREATE (d:Person {name: 'Diana', age: 28, city: 'NYC'})-[:KNOWS {since: 2021}]->(e:Person {name: 'Eve', age: 32, city: 'LA'});
CREATE (f:Person {name: 'Frank', age: 40, city: 'SF'})-[:FRIENDS {since: 2022}]->(:Person {name: 'Grace', age: 22, city: 'NYC'});

MATCH (n:Person)
WHERE n.age > 25
WITH n, n.city AS city
MATCH (n)-[r]->(m)
WHERE m:Person AND m.age < 35
WITH n, r, m, city
OPTIONAL MATCH (n)-[r2]->(o:Person)
WHERE o.age > 20
WITH n, r, m, city, o, r2
WHERE size([(n)-->(p) WHERE p:Person | p]) > 1
WITH n, r, m, city, o, r2, collect(o) AS others
RETURN n.name AS source, type(r) AS rel, m.name AS target, city, [x IN others | x.name] AS other_names
UNION ALL
MATCH (n:Person)
WHERE n.age <= 25
WITH n, n.city AS city
MATCH (n)-[r]->(m)
WHERE m:Person AND m.age >= 35
WITH n, r, m, city
OPTIONAL MATCH (n)-[r2]->(o:Person)
WHERE o.age > 20
WITH n, r, m, city, o, r2
WHERE size([(n)-->(p) WHERE p:Person | p]) > 1
WITH n, r, m, city, o, r2, collect(o) AS others
RETURN n.name AS source, type(r) AS rel, m.name AS target, city, [x IN others | x.name] AS other_names;
```

Observed Neo4j result:

```text
0 rows
```

Observed Memgraph result:

```text
source = Alice, rel = KNOWS, target = Bob, city = NYC, other_names = ['Bob']
```

And again, the same query realigns on both engines if the inline filter is first materialized, for example:

```cypher
WITH n, r, m, city, o, r2, size([(n)-->(p) WHERE p:Person | p]) AS c
WHERE c > 1
```

or:

```cypher
WITH n, r, m, city, o, r2, [(n)-->(p) WHERE p:Person | p] AS hits
WHERE size(hits) > 1
```

In both control forms, Neo4j and Memgraph return `0 rows`. So this `UNION ALL` case is consistent with the same inline correlation-loss issue rather than a separate union-specific family.

**Differential seed**
This family was surfaced in:

- `ArcadeData__arcadedb__3404__q001`
- `case_1d6ebfa5afb6`

**Verification environment**
Verified directly against:

- Neo4j Docker container: `neo4j:2026.04.0`
- Memgraph Docker container: `memgraph/memgraph:3.9.0`
- Memgraph Docker container: `memgraph/memgraph:latest` (resolved locally to `3.10.1`)
- local differential-testing harness
- direct fresh reruns over Bolt
