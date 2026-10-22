**Suggested title**
`Nested pattern comprehensions inside an outer list comprehension may duplicate matches`

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
Memgraph may overcount matches when a pattern comprehension is nested inside an outer list comprehension over a node list.

In the minimized repro below, both `Alice` and `Bob` each have exactly one outgoing `WORKS_AT` relationship to the same company.

Neo4j returns:

```text
[1, 1]
```

because each node contributes exactly one inner match.

Memgraph returns:

```text
[2, 2]
```

so each inner pattern comprehension is evaluated as if the single match were duplicated.

This does not look like a generic pattern-comprehension problem, because the same inner pattern comprehension works correctly when evaluated directly for `a` and `b`, and it also works correctly if the outer list is unnested with `UNWIND`.

**To Reproduce**
1. Start the Memgraph Docker container.
2. Run:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Person {name: 'Alice'})-[:KNOWS]->(b:Person {name: 'Bob'}),
       (a)-[:WORKS_AT]->(c:Company {name: 'Acme'}),
       (b)-[:WORKS_AT]->(c);

MATCH (a:Person {name:'Alice'})-[:KNOWS]->(b:Person {name:'Bob'})
RETURN [n IN [a, b] | size([(n)-[:WORKS_AT]->(t) | t.name])] AS counts;
```

**Expected behavior**
Observed Neo4j result:

```text
[1, 1]
```

Memgraph should return the same list.

**Actual behavior**
Observed Memgraph result:

```text
[2, 2]
```

Each inner pattern comprehension is overcounted.

**Control query 1**
If the same inner pattern comprehension is evaluated directly, both engines behave correctly:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Person {name: 'Alice'})-[:KNOWS]->(b:Person {name: 'Bob'}),
       (a)-[:WORKS_AT]->(c:Company {name: 'Acme'}),
       (b)-[:WORKS_AT]->(c);

MATCH (a:Person {name:'Alice'})-[:KNOWS]->(b:Person {name:'Bob'})
RETURN size([(a)-[:WORKS_AT]->(t) | t.name]) AS acnt,
       size([(b)-[:WORKS_AT]->(t) | t.name]) AS bcnt;
```

Observed result on both Neo4j and Memgraph:

```text
1, 1
```

So the inner pattern comprehension itself can work.

**Control query 2**
If the outer list is unnested into rows first, both engines also behave correctly:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Person {name: 'Alice'})-[:KNOWS]->(b:Person {name: 'Bob'}),
       (a)-[:WORKS_AT]->(c:Company {name: 'Acme'}),
       (b)-[:WORKS_AT]->(c);

MATCH (a:Person {name:'Alice'})-[:KNOWS]->(b:Person {name:'Bob'})
UNWIND [a, b] AS n
RETURN n.name AS name,
       size([(n)-[:WORKS_AT]->(t) | t.name]) AS cnt
ORDER BY name;
```

Observed result on both Neo4j and Memgraph:

```text
Alice, 1
Bob,   1
```

So the problem is not simply "pattern comprehension over a matched node".

**Control query 3**
If the outer list comprehension does not contain an inner pattern comprehension, both engines also line up:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Person {name: 'Alice'})-[:KNOWS]->(b:Person {name: 'Bob'}),
       (a)-[:WORKS_AT]->(c:Company {name: 'Acme'}),
       (b)-[:WORKS_AT]->(c);

MATCH (a:Person {name:'Alice'})-[:KNOWS]->(b:Person {name:'Bob'})
RETURN [n IN [a, b] | 1] AS counts;
```

Observed result on both Neo4j and Memgraph:

```text
[1, 1]
```

So the issue is not a generic outer list-comprehension bug either.

**Stronger reproducer**
The same duplication also appears if the inner pattern comprehension result itself is returned instead of its size:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Person {name: 'Alice'})-[:KNOWS]->(b:Person {name: 'Bob'}),
       (a)-[:WORKS_AT]->(c:Company {name: 'Acme'}),
       (b)-[:WORKS_AT]->(c);

MATCH (a:Person {name:'Alice'})-[:KNOWS]->(b:Person {name:'Bob'})
RETURN [n IN [a, b] | [(n)-[:WORKS_AT]->(t) | t.name]] AS x;
```

Observed Neo4j result:

```text
[['Acme'], ['Acme']]
```

Observed Memgraph result:

```text
[['Acme', 'Acme'], ['Acme', 'Acme']]
```

So the wrong-result family is not tied to `size(...)`; the duplicated inner matches are already present in the nested pattern-comprehension result.

**Additional supporting repro**
The same family also appears when the outer list comes from `nodes(p)` on a path rather than from a literal `[a, b]` list:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Person {name: 'Alice', age: 30})-[:KNOWS]->(b:Person {name: 'Bob', age: 25})-[:KNOWS]->(c:Person {name: 'Charlie', age: 35});
CREATE (a)-[:FRIEND]->(d:Person {name: 'David', age: 28});
CREATE (b)-[:FRIEND]->(e:Person {name: 'Eve', age: 22});

MATCH p = (n1:Person {name: 'Alice'})-[*..3]-(n2)
RETURN [n IN nodes(p) | [(n)-[:KNOWS]->(t) | t.name]] AS result;
```

Observed results:

- Neo4j:

```text
[['Bob'], ['Charlie']]
[['Bob'], ['Charlie'], []]
```

- Memgraph:

```text
[['Bob', 'Charlie'], ['Bob', 'Charlie']]
[['Bob', 'Charlie'], ['Bob', 'Charlie'], ['Bob', 'Charlie']]
```

If the same outer list is first unnested into rows, both engines line up again:

```cypher
MATCH p = (n1:Person {name: 'Alice'})-[*..3]-(n2)
UNWIND nodes(p) AS n
RETURN n.name AS name, [(n)-[:KNOWS]->(t) | t.name] AS result
ORDER BY name;
```

Observed result on Neo4j and Memgraph:

```text
Alice,   ['Bob']
Bob,     ['Charlie']
Charlie, []
```

So the duplicated inner matches are not tied to the specific literal `[a, b]` outer list; they also appear when the outer list is derived from path nodes.

**Differential seed**
This family surfaced while minimizing:

- `FalkorDB__FalkorDB__338__q004`
- `FalkorDB__FalkorDB__338__q001` / `case_986db660a00e`

**Verification Environment**
Plain Memgraph Docker image is enough.

Preferred verification target:
- Docker image
- x86_64
- `memgraph/memgraph`

I can verify a fix using:
- plain Memgraph Docker image on Windows host
- repo Stage4 executor / direct Bolt execution
