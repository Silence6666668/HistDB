**Suggested title**
`Repeated self-referential property updates on the same node across row fanout may apply only the last row`

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
- Request mode matches ArcadeDB Studio:
  - `language: opencypher`
  - `serializer: studio`
- Differential comparison target: Neo4j Docker `neo4j:latest`

**Describe the bug**
When the same node is hit on multiple rows in a single statement, a self-referential property update such as:

```cypher
SET p.age = p.age + 1
```

should accumulate once per row.

Neo4j does that.
ArcadeDB appears to apply only the last row's increment.

In the minimized repro below, the same `Person` row is revisited three times by `UNWIND [1, 2, 3]`.
Neo4j persists the final age as `36`.
ArcadeDB persists `33`, which is consistent with only the last `+3` being applied.

To avoid ambiguity about how mutating queries materialize values in their own `RETURN`, the repro checks the final persisted state with a separate read query.

**To Reproduce**

Setup:

```cypher
CREATE (:Person {name:'Alice', age:30});
```

Mutation query:

```cypher
MATCH (p:Person {name:'Alice'})
UNWIND [1, 2, 3] AS i
SET p.age = p.age + i;
```

Check query:

```cypher
MATCH (p:Person {name:'Alice'})
RETURN p.age AS age;
```

**Expected behavior**
The three updates should accumulate:

- start at `30`
- apply `+1`, then `+2`, then `+3`
- final persisted value should be `36`

Observed Neo4j result:

```text
36
```

**Actual behavior**
Observed ArcadeDB result on all tested versions:

```text
33
```

So ArcadeDB is not accumulating all row hits.
It looks like only the last row update is persisted.

**Control cases**

A single-row self-referential update behaves normally:

```cypher
MATCH (p:Person {name:'Alice'})
SET p.age = p.age + 3;

MATCH (p:Person {name:'Alice'})
RETURN p.age AS age;
```

Observed result on Neo4j and all tested ArcadeDB versions:

```text
33
```

So ordinary self-referential `SET` is not broken in general.

If the repeated updates do not depend on the previous property value, ArcadeDB also behaves normally:

```cypher
MATCH (p:Person {name:'Alice'})
UNWIND [1, 2, 3] AS i
SET p.age = i;

MATCH (p:Person {name:'Alice'})
RETURN p.age AS age;
```

Observed result on Neo4j and all tested ArcadeDB versions:

```text
3
```

This makes the boundary sharper:

- repeated row hits alone are not enough
- repeated constant assignment is fine
- repeated self-referential updates like `p.age = p.age + ...` are what fail

The same family also appears when the increment is constant on each row:

```cypher
MATCH (p:Person {name:'Alice'})
UNWIND [1, 2, 3] AS i
SET p.age = p.age + 1;

MATCH (p:Person {name:'Alice'})
RETURN p.age AS age;
```

Observed results:

- Neo4j: `33`
- ArcadeDB: `31`

So this does not depend on the increment values being different per row.

**Stronger reproducer**

The issue is not limited to one property on one node.
It also shows up when two bound nodes are updated together across the same row fanout:

Setup:

```cypher
CREATE (:Person {name:'Alice', age:30}),
       (:Company {name:'TechCorp', founded:2000});
```

Mutation query:

```cypher
MATCH (p:Person {name:'Alice'}), (c:Company {name:'TechCorp'})
UNWIND [1, 2, 3] AS i
SET p.age = p.age + i,
    c.founded = c.founded - i;
```

Check query:

```cypher
MATCH (p:Person {name:'Alice'}), (c:Company {name:'TechCorp'})
RETURN p.age AS age, c.founded AS founded;
```

Observed results:

- Neo4j: `36`, `1994`
- ArcadeDB: `33`, `1997`

So the problem is not tied to one specific property or label.

**Differential seed**
This family first surfaced while minimizing:

- `RedisGraph__RedisGraph__2865__q007`

Original generated query:

```cypher
MATCH (p {name: 'Alice'}), (c {name: 'TechCorp'})
WITH p, c
UNWIND [1, 2, 3] AS i
MERGE (p)-[:WORKS_AT {since: 2020, remote: true}]->(c)
SET p.age = p.age + i, c.founded = c.founded - i
RETURN p.name, p.age, c.name, c.founded
```

Direct reduction shows the `MERGE` is not the core issue.
The smaller `UNWIND ... SET p.age = p.age + i` reproducer above is already enough.

**Verification environment**
Verified directly against:

- Neo4j Docker container: `neo4j:latest`
- ArcadeDB Docker container: `arcadedata/arcadedb:latest`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.3.2`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.4.1-SNAPSHOT`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.4.2`
- Local differential-testing harness
- Direct ArcadeDB HTTP execution using the same request mode as Studio
