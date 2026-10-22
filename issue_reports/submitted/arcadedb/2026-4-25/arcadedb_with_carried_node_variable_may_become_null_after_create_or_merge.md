**Suggested title**
`WITH`-carried node variables may become `null` after a later `CREATE` or `MERGE`

**ArcadeDB version**
Observed on Docker images:

- `arcadedata/arcadedb:latest`
- `arcadedata/arcadedb:26.3.2`
- `arcadedata/arcadedb:26.4.1-SNAPSHOT`
- `arcadedata/arcadedb:26.4.2`

**Discovered on**
- `2026-04-25`

**Environment**
- Host OS: Windows
- Architecture: x86_64
- Deployment: Docker
- ArcadeDB endpoint: HTTP `/api/v1/command/arcade`
- Request mode used for direct reproduction:
  - `language: opencypher`
  - `serializer: studio`
- Differential comparison target: Neo4j Docker `neo4j:latest`

**Describe the bug**
ArcadeDB may null out a node variable that was correctly carried through `WITH` once a later write clause creates or merges some other node.

In the minimized repro below, `n` is already bound by `MATCH`, then passed through `WITH n`, and the later `CREATE` writes an unrelated `Temp` node.

Neo4j still returns `n.name`.
ArcadeDB returns `null` for `n.name`.

This does not look like a general `WITH` problem:

- `WITH n RETURN n.name` works
- `WITH n.name AS name CREATE ... RETURN name` works
- `WITH n SET n.flag = true RETURN n.name` also works

So the boundary appears to be narrower: carried node variables are lost specifically after a later `CREATE` or `MERGE`, while scalar aliases and in-place `SET` remain fine.

**To Reproduce**

Setup:

```cypher
CREATE (:Person {name:'Alice'}),
       (:Person {name:'Bob'});
```

Query:

```cypher
MATCH (n:Person)
WITH n
CREATE (:Temp {x:1})
RETURN n.name AS name
ORDER BY name;
```

**Expected behavior**
The unrelated `CREATE` should not affect the already carried node binding.

Observed Neo4j result:

```text
Alice
Bob
```

**Actual behavior**
Observed ArcadeDB result:

```text
null
null
```

So the carried node variable `n` is still present enough to keep the row count, but its later property access behaves as if `n` became `null`.

**Control cases**

Without the later write clause, the carried node variable behaves correctly:

```cypher
MATCH (n:Person)
WITH n
RETURN n.name AS name
ORDER BY name;
```

Observed result on Neo4j and ArcadeDB:

```text
Alice
Bob
```

If only a scalar alias is carried through, ArcadeDB also behaves correctly even with the later `CREATE`:

```cypher
MATCH (n:Person)
WITH n.name AS name
CREATE (:Temp {x:2})
RETURN name
ORDER BY name;
```

Observed result on Neo4j and ArcadeDB:

```text
Alice
Bob
```

So the problem is not that all carried values are lost after `CREATE`.
It is specifically the carried node variable that is affected.

If the later write is an in-place `SET` on the same node, ArcadeDB also behaves correctly:

```cypher
MATCH (n:Person)
WITH n
SET n.flag = true
RETURN n.name AS name
ORDER BY name;
```

Observed result on Neo4j and ArcadeDB:

```text
Alice
Bob
```

This makes the boundary clearer:

- carried node variable + later `CREATE`: incorrect
- carried node variable + later `MERGE`: incorrect
- carried node variable + later `SET`: correct
- carried scalar alias + later `CREATE`: correct

**Stronger reproducer**

The same issue also appears with `MERGE`, not just `CREATE`:

```cypher
MATCH (n:Person)
WITH n
MERGE (:Temp {x:10})
RETURN n.name AS name
ORDER BY name;
```

Observed Neo4j result:

```text
Alice
Bob
```

Observed ArcadeDB result:

```text
null
null
```

And if both a node variable and a scalar are carried together, only the node variable is lost:

```cypher
MATCH (n:Person)
WITH n, 1 AS x
CREATE (:Temp {x:3})
RETURN n.name AS name, x
ORDER BY name;
```

Observed Neo4j result:

```text
Alice, 1
Bob,   1
```

Observed ArcadeDB result:

```text
null, 1
null, 1
```

So the scalar `x` survives while the node variable `n` does not.

**Differential seed**
This family surfaced while minimizing a carry-through write pattern related to:

- `neo4j__13197__q001`

That generated query collected relationship values before a later `MERGE`, and ArcadeDB returned wrong post-write counts.
Direct reduction shows a simpler core issue: a node variable carried through `WITH` can become unusable after an unrelated `CREATE` or `MERGE`.

**Verification environment**
Verified directly against:

- Neo4j Docker container: `neo4j:latest`
- ArcadeDB Docker container: `arcadedata/arcadedb:latest`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.3.2`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.4.1-SNAPSHOT`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.4.2`
- Local differential-testing harness
- Direct ArcadeDB HTTP execution
