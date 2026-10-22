**Suggested title**
`Deprecated CALL write subquery without explicit import may nullify outer variables afterward`

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
Memgraph may nullify an outer variable after a deprecated-style `CALL { ... }` subquery if:

- the outer variable is carried into the subquery implicitly from the surrounding pipeline,
- the subquery body performs a write, and
- the subquery does **not** explicitly re-import the outer variable with `WITH`.

In the minimized repro below, Neo4j returns `Alice`. Memgraph instead returns `null`, even though the outer variable `a` was already bound before entering the subquery.

This does not look like a general problem with `CALL { ... }`, because Memgraph behaves normally if the subquery is read-only, or if the write subquery explicitly imports `a` (and `b`) with `WITH`.

**To Reproduce**
1. Start the Memgraph Docker container.
2. Run:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice'});
CREATE (:Person {name:'Bob'});

MATCH (a:Person {name:'Alice'})
MATCH (b:Person {name:'Bob'})
WITH a, b
CALL {
  CREATE (a)-[:LIKES]->(b)
}
RETURN a.name AS name;
```

**Expected behavior**
Observed Neo4j result:

```text
name = Alice
```

Memgraph should return the same value.

**Actual behavior**
Observed Memgraph result:

```text
name = null
```

So the outer variable remains present as a row, but its value is lost after the write subquery.

**Control query 1**
If the same write is done without `CALL`, both engines behave normally:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice'});
CREATE (:Person {name:'Bob'});

MATCH (a:Person {name:'Alice'})
MATCH (b:Person {name:'Bob'})
CREATE (a)-[:LIKES]->(b)
RETURN a.name AS name;
```

Observed result on Neo4j and Memgraph:

```text
name = Alice
```

So the problem is not simply “write followed by return of a bound node”.

**Control query 2**
If the subquery explicitly re-imports the outer variables, both engines also behave normally:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice'});
CREATE (:Person {name:'Bob'});

MATCH (a:Person {name:'Alice'})
MATCH (b:Person {name:'Bob'})
WITH a, b
CALL {
  WITH a, b
  CREATE (a)-[:LIKES]->(b)
}
RETURN a.name AS name;
```

Observed result on Neo4j and Memgraph:

```text
name = Alice
```

This makes the boundary much sharper: the failure is tied to the deprecated implicit-import form, not to the write itself.

**Control query 3**
If the deprecated-style subquery is read-only, both engines also behave normally:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice'});
CREATE (:Person {name:'Bob'});

MATCH (a:Person {name:'Alice'})
MATCH (b:Person {name:'Bob'})
WITH a, b
CALL {
  RETURN 1 AS x
}
RETURN a.name AS name;
```

Observed result on Neo4j and Memgraph:

```text
name = Alice
```

So the issue is not a blanket inability to preserve outer variables across deprecated-style `CALL`.

**Additional variant**
The same outer-variable nullification also appears with a single outer variable and a self-relationship write:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice'});

MATCH (a:Person {name:'Alice'})
WITH a
CALL {
  CREATE (a)-[:SELF]->(a)
}
RETURN a.name AS name;
```

Observed Neo4j result:

```text
name = Alice
```

Observed Memgraph result:

```text
name = null
```

This suggests the issue is not specific to the presence of a second variable `b`.

**Related larger differential query**
The original larger case that exposed the family was:

```cypher
MATCH (a:Person {name: 'Alice'})
MATCH (b:Person {name: 'Bob'})
CREATE (a)-[:KNOWS]->(b)
WITH a, b
CALL {
  CREATE (a)-[:LIKES]->(b)
}
CREATE (a)-[:FRIEND]->(b)
CREATE (a)-[:SELF]->(a)
SET a.updated = true
RETURN a.name;
```

Observed result summary:

- Neo4j returned `Alice`
- Memgraph returned `null`

The larger write sequence reduces cleanly to the smaller reproducer above.

**Differential seed**
This family was surfaced while minimizing:

- `FalkorDB__FalkorDB__367__q002`
- `case_5451193665fc`

**Verification environment**
Verified directly against:

- Neo4j Docker container: `neo4j:2026.04.0`
- Memgraph Docker container: `memgraph/memgraph:3.9.0`
- Memgraph Docker container: `memgraph/memgraph:latest` (resolved locally to `3.10.1`)
- local differential-testing harness
- direct fresh reruns over Bolt
