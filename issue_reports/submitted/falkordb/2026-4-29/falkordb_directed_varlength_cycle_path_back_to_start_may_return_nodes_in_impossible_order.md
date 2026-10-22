done
**Suggested title**
`Directed variable-length cycle path back to the start node may return nodes in an impossible order`

**FalkorDB version**
Observed on:

- FalkorDB `4.18.1`

Docker image used locally:
- `falkordb/falkordb:latest`

**Discovered on**
- `2026-04-24`

**Environment**
- Host OS: Windows
- Architecture: x86_64
- Deployment: Docker
- Connection method: RESP / `GRAPH.QUERY`
- Client used for direct reproduction: project RESP executor + direct `GRAPH.QUERY`
- Differential comparison target: Neo4j Community `2026.02.2`

**Describe the bug**
FalkorDB may return a self-returning directed variable-length cycle path with its interior nodes in the wrong order.

In the minimized repro below, the graph is a simple directed 3-cycle:

- `Alice -> Bob`
- `Bob -> Charlie`
- `Charlie -> Alice`

The exact 3-hop path from `Alice` back to `Alice` should therefore be:

```text
Alice -> Bob -> Charlie -> Alice
```

Neo4j returns that path.
FalkorDB instead returns:

```text
Alice -> Charlie -> Bob -> Alice
```

which is impossible in this graph under the directed `[:KNOWS*3..3]` pattern.

This does not look like a general variable-length problem, because the non-self-returning directed path from `Alice` to `Charlie` is returned correctly on both engines. The bad behavior appears when the directed cycle comes back to the start node.

**To Reproduce**
1. Start the FalkorDB Docker container.
2. Run:

```cypher
CREATE (a:Person {name:'Alice'}),
       (b:Person {name:'Bob'}),
       (c:Person {name:'Charlie'}),
       (a)-[:KNOWS {step: 1}]->(b),
       (b)-[:KNOWS {step: 2}]->(c),
       (c)-[:KNOWS {step: 3}]->(a);

MATCH path = (:Person {name:'Alice'})-[:KNOWS*3..3]->(:Person {name:'Alice'})
RETURN [n IN nodes(path) | n.name] AS pathNames,
       [rel IN relationships(path) | rel.step] AS stepOrder;
```

**Expected behavior**
Neo4j returns:

```text
pathNames = [Alice, Bob, Charlie, Alice]
stepOrder = [1, 2, 3]
```

FalkorDB should return the same path.

**Actual behavior**
FalkorDB returns:

```text
pathNames = [Alice, Charlie, Bob, Alice]
stepOrder = [1, 2, 3]
```

So the node sequence is reversed in the middle even though the relationship sequence still reports the forward `step` order `[1, 2, 3]`.

That makes the returned path internally inconsistent: the node list and relationship list no longer describe the same directed path.

**Control query 1**
The non-self-returning directed path is still correct on both engines:

```cypher
CREATE (a:Person {name:'Alice'}),
       (b:Person {name:'Bob'}),
       (c:Person {name:'Charlie'}),
       (a)-[:KNOWS {step: 1}]->(b),
       (b)-[:KNOWS {step: 2}]->(c),
       (c)-[:KNOWS {step: 3}]->(a);

MATCH path = (:Person {name:'Alice'})-[:KNOWS*2..2]->(:Person {name:'Charlie'})
RETURN [n IN nodes(path) | n.name] AS pathNames,
       [rel IN relationships(path) | rel.step] AS stepOrder;
```

Observed result on both Neo4j and FalkorDB:

```text
pathNames = [Alice, Bob, Charlie]
stepOrder = [1, 2]
```

So the issue is not general directed path binding. It is tied to the self-returning cycle case.

**Control query 2**
The bug also reproduces in a pure read query without any later write:

```cypher
CREATE (a:Person {name:'Alice', age: 30}),
       (b:Person {name:'Bob', age: 25}),
       (c:Person {name:'Charlie', age: 35}),
       (a)-[:KNOWS]->(b),
       (b)-[:KNOWS]->(c),
       (c)-[:KNOWS]->(a);

MATCH path = (a:Person)-[:KNOWS*0..]->(b:Person)
WHERE a.name = 'Alice'
RETURN b.name AS known_person,
       [node IN nodes(path) | node.name] AS path_names
ORDER BY known_person;
```

Observed Neo4j rows:

```text
Alice, [Alice]
Alice, [Alice, Bob, Charlie, Alice]
Bob,   [Alice, Bob]
Charlie, [Alice, Bob, Charlie]
```

Observed FalkorDB rows:

```text
Alice, [Alice]
Alice, [Alice, Charlie, Bob, Alice]
Bob,   [Alice, Bob]
Charlie, [Alice, Bob, Charlie]
```

So this is not caused by a later `MERGE` or update side effect. The pure read path itself is already wrong on FalkorDB.

**Original differential seed**
The family was first noticed in:

- `FalkorDB__FalkorDB__443__q002`

That original seed additionally performs a later `MERGE`, but the pure-read control above shows the root issue is already present before the write.

**Why this matters**
This is more than a presentation difference.

For a path value, `nodes(path)` and `relationships(path)` are supposed to describe the same walk. In the repro above, FalkorDB returns:

- node order corresponding to `Alice -> Charlie -> Bob -> Alice`
- relationship order corresponding to `Alice -> Bob -> Charlie -> Alice`

So client code that relies on path semantics can observe a path object that is internally inconsistent.

**Verification Environment**
Plain FalkorDB Docker image is enough.

Preferred verification target:
- Docker image
- x86_64
- `falkordb/falkordb`

I can verify a fix using:
- plain FalkorDB Docker image on Windows host
- project RESP executor / direct `GRAPH.QUERY`
