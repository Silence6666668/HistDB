done
**Suggested title**
`List-comprehension filter may return null when the predicate evaluates to null for some elements`

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
Memgraph may evaluate a list comprehension incorrectly when:

- the input list contains an element for which the `WHERE` predicate evaluates to `null`, and
- the comprehension should therefore filter that element out and continue

Instead of treating that element as non-matching, Memgraph may return `null` for the whole comprehension result.

In the minimized repro below, Neo4j treats the missing property access as a non-matching element and returns:

```text
['Charlie']
```

Memgraph instead returns:

```text
null
```

This does not look like a general problem with mixed node lists or property projection, because nearby controls behave normally once the missing-property case is avoided explicitly.

**To Reproduce**
1. Start the Memgraph Docker container.
2. Run:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Person {name:'Alice'})-[:KNOWS]->(p:Person {name:'Charlie', age:35}),
       (a)-[:LIKES]->(i:Item {name:'Widget'});

MATCH (a:Person {name:'Alice'})-[:KNOWS]->(p:Person),
      (a)-[:LIKES]->(i:Item)
RETURN [x IN [p, i] WHERE x.age > 25 | x.name] AS names;
```

**Expected behavior**
Observed Neo4j result:

```text
['Charlie']
```

Memgraph should return the same list. The `Item` node does not have `age`, so it should simply fail the filter rather than nullify the whole comprehension result.

**Actual behavior**
Observed Memgraph result:

```text
null
```

So Memgraph appears to propagate the `null` predicate result through the comprehension filter in a way that nullifies the whole list result.

**Control query 1**
If the same mixed node list is filtered using an explicit null check instead of `x.age > 25`, both engines behave normally:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Person {name:'Alice'})-[:KNOWS]->(p:Person {name:'Charlie', age:35}),
       (a)-[:LIKES]->(i:Item {name:'Widget'});

MATCH (a:Person {name:'Alice'})-[:KNOWS]->(p:Person),
      (a)-[:LIKES]->(i:Item)
RETURN [x IN [p, i] WHERE x.age IS NOT NULL | x.name] AS names;
```

Observed result on Neo4j and Memgraph:

```text
['Charlie']
```

So mixed node lists themselves are not the issue.

**Control query 2**
If the `Item` node is given an `age` property, both engines also behave normally with the original numeric predicate:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Person {name:'Alice'})-[:KNOWS]->(p:Person {name:'Charlie', age:35}),
       (a)-[:LIKES]->(i:Item {name:'Widget', age:0});

MATCH (a:Person {name:'Alice'})-[:KNOWS]->(p:Person),
      (a)-[:LIKES]->(i:Item)
RETURN [x IN [p, i] WHERE x.age > 25 | x.name] AS names;
```

Observed result on Neo4j and Memgraph:

```text
['Charlie']
```

This makes the boundary clearer: the issue is tied specifically to the missing property on one node in the mixed list.

**Control query 3**
If the list contains only `Person` nodes, both engines also line up:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Person {name:'Alice', age:30})-[:KNOWS]->(:Person {name:'Charlie', age:35}),
       (a)-[:KNOWS]->(:Person {name:'Bob', age:25});

MATCH (a:Person {name:'Alice'})-[:KNOWS]->(p:Person)
RETURN [x IN collect(p) WHERE x.age > 25 | x.name] AS names;
```

Observed result on Neo4j and Memgraph:

```text
['Charlie']
```

So the problem is not a generic issue with `x.age > 25` inside a list comprehension.

**Stronger reproducer**
The same family also appears in the richer differential case that first exposed it:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Person {name: 'Alice', age: 30, city: 'NYC'})-[:KNOWS]->(b:Person {name: 'Bob', age: 25, city: 'LA'})-[:LIKES]->(c:Item {name: 'Widget', price: 10}),
       (a)-[:LIKES]->(c),
       (b)-[:KNOWS]->(d:Person {name: 'Charlie', age: 35, city: 'SF'}),
       (d)-[:LIKES]->(c),
       (a)-[:FRIEND]->(e:Person {name: 'Diana', age: 28, city: 'NYC'});

MATCH (p:Person {name: 'Alice'})
OPTIONAL MATCH (p)-[:KNOWS|LIKES*1..2]->(q)
WITH p, collect(DISTINCT q) AS neighbors,
     [x IN collect(DISTINCT q) WHERE x.age > 25 | x.name + ' from ' + x.city] AS names
WHERE size(names) > 0
RETURN p.name AS person, size(neighbors) AS neighborCount, names[0] AS firstNeighbor;
```

Observed Neo4j result:

```text
Alice, 3, 'Charlie from SF'
```

Observed Memgraph result:

```text
0 rows
```

This larger form is consistent with the minimized repro: once the comprehension result becomes `null`, the later `WHERE size(names) > 0` removes the row.

**Additional variant with label predicates**
The same family also appears when the predicate is a label test over a list containing `null`:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice'}), (:Person {name:'Bob'});

MATCH (a:Person {name:'Alice'}), (b:Person {name:'Bob'})
WITH a, b, null AS c
RETURN [x IN [a, b, c] WHERE x:Person | x.name] AS names;
```

Observed results:

- Neo4j:

```text
['Alice', 'Bob']
```

- Memgraph:

```text
null
```

This shows the problem is not limited to missing-property predicates such as `x.age > 25`. A label predicate that evaluates to `null` on one element can also nullify the whole comprehension.

If the same filter is rewritten to avoid a `null` predicate result, both engines behave normally:

```cypher
MATCH (a:Person {name:'Alice'}), (b:Person {name:'Bob'})
WITH a, b, null AS c
RETURN [x IN [a, b, c] WHERE x IS NOT NULL | x.name] AS names;
```

Observed result on Neo4j and Memgraph:

```text
['Alice', 'Bob']
```

**Differential seed**
This family was surfaced in:

- `ArcadeData__arcadedb__3560__q003`
- `case_9513cf9ab154`
- `RedisGraph__RedisGraph__2643__q004`
- `case_8b3e28ec55bb`

**Verification environment**
Verified directly against:

- Neo4j Docker container: `neo4j:2026.04.0`
- Memgraph Docker container: `memgraph/memgraph:3.9.0`
- Memgraph Docker container: `memgraph/memgraph:latest` (resolved locally to `3.10.1`)
- local differential-testing harness
- direct fresh reruns over Bolt
