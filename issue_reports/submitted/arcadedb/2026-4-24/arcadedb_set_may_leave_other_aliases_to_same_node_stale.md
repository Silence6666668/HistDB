done
**Suggested title**
`SET may leave other aliases to the same node stale within the same query`

**ArcadeDB version**
Observed on Docker images:

- `arcadedata/arcadedb:latest`
- `arcadedata/arcadedb:26.3.2`
- `arcadedata/arcadedb:26.4.1-SNAPSHOT`
- `arcadedata/arcadedb:26.4.2`

The current local `latest` container contains ArcadeDB `26.4.1-SNAPSHOT` jars.

**Discovered on**
- `2026-04-24`

**Environment**
- Host OS: Windows
- Architecture: x86_64
- Deployment: Docker
- ArcadeDB endpoint: HTTP `/api/v1/command/arcade`
- Request mode matches ArcadeDB Studio:
  - `language: opencypher`
  - `serializer: studio`
- Differential comparison target: Neo4j Docker `neo4j:latest`

**Describe the bug**
If two variables are already bound to the same node, an in-query `SET` on one alias may not be reflected through the other alias on ArcadeDB.

In Neo4j, both aliases observe the updated properties because they refer to the same underlying node.
On ArcadeDB, the written alias shows the new value, while the other alias still exposes the old value.

This makes the row internally inconsistent: the same node appears with two different property snapshots in the same result.

**To Reproduce**

Setup:

```cypher
CREATE (:Person {name:'Alice', age:30}),
       (:Person {name:'Bob', age:25});
```

Query:

```cypher
MATCH (n:Person {name:'Alice'}), (m:Person {name:'Alice'})
SET n.age = 35
RETURN n.age AS n_age, m.age AS m_age;
```

**Expected behavior**
Both aliases refer to the same node, so both should observe the update:

```text
35, 35
```

Observed Neo4j result:

```text
35, 35
```

**Actual behavior**
ArcadeDB returns:

```text
35, 30
```

So `n` shows the updated value, but `m` remains stale even though both variables point to the same node.

**Control cases**

Using only one alias works correctly:

```cypher
MATCH (n:Person {name:'Alice'})
SET n.age = 35
RETURN n.age AS n_age;
```

Observed result on Neo4j and ArcadeDB:

```text
35
```

If the second alias is rebound after the write, ArcadeDB also behaves correctly:

```cypher
MATCH (n:Person {name:'Alice'})
SET n.age = 35
WITH n
MATCH (m:Person {name:'Alice'})
RETURN n.age AS n_age, m.age AS m_age;
```

Observed result on Neo4j and ArcadeDB:

```text
35, 35
```

If the second alias points to a different node, ArcadeDB is also fine:

```cypher
MATCH (n:Person {name:'Alice'}), (m:Person {name:'Bob'})
SET n.age = 35
RETURN n.age AS n_age, m.age AS m_age;
```

Observed result on Neo4j and ArcadeDB:

```text
35, 25
```

This makes the boundary much clearer:
- writing through one alias works
- reading through a fresh re-match works
- reading through a different node works
- the issue is specifically that another alias already bound to the same node remains stale

**Stronger reproducer**
The same stale-alias effect also shows up when returning full nodes:

```cypher
MATCH (n:Person {name:'Alice'})
WITH n
MATCH (m:Person {name:'Alice'})
SET n.age = 35
RETURN n, m;
```

Observed Neo4j result:
- both `n` and `m` show `age: 35`

Observed ArcadeDB result:
- `n` shows `age: 35`
- `m` still shows `age: 30`

So this is not just an issue in scalar projection.
ArcadeDB appears to materialize stale entity snapshots for already-bound aliases.

**Differential seed**
This family was first surfaced while minimizing an ArcadeDB runtime difference derived from:

- `ArcadeData__arcadedb__3278__q002`

**Verification environment**
Verified directly against:

- Neo4j Docker container: `neo4j:latest`
- ArcadeDB Docker container: `arcadedata/arcadedb:latest`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.3.2`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.4.1-SNAPSHOT`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.4.2`
- Local differential-testing harness
- Direct ArcadeDB HTTP execution using the same request mode as Studio
