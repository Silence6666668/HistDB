**Suggested title**
`Repeated SET label on the same node across row fanout may fail instead of acting idempotently`

**ArcadeDB version**
Observed on Docker images:

- `arcadedata/arcadedb:latest`
- `arcadedata/arcadedb:26.3.2`
- `arcadedata/arcadedb:26.4.1-SNAPSHOT`
- `arcadedata/arcadedb:26.4.2`

The failure mode changes by version:

- `latest`: duplicate-key failure while reapplying the new label
- `26.3.2`: `Record #... not found`
- `26.4.1-SNAPSHOT` and `26.4.2`: `Record #... not found`

So this looks like one stable semantic family with different internal failure modes.

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
When the same node appears on multiple rows in a single query, reapplying a label with `SET n:Label` should act idempotently.

Neo4j handles this normally: if the same node is reached twice, the first row adds the label and later rows keep going.

ArcadeDB instead fails as soon as the same node is hit again by the same label-setting operation.

This means ArcadeDB may crash on ordinary row fanout shapes where a label is applied after a join, cartesian expansion, or any other pattern that can duplicate a bound node.

**To Reproduce**

Setup:

```cypher
CREATE (:Person {name:'Alice', age:30}),
       (:Person {name:'Bob', age:25}),
       (:Person {name:'Charlie', age:35});
```

Query:

```cypher
MATCH (n:Person)
MATCH (m:Person)
WHERE n.age > m.age
SET n:Employee
RETURN n.name AS name
ORDER BY name;
```

**Expected behavior**
The same node may appear on multiple rows, but adding the same label repeatedly should still be harmless.

Observed Neo4j result:

```text
Alice
Charlie
Charlie
```

ArcadeDB should also return the rows successfully.

**Actual behavior**
Observed ArcadeDB results:

- `latest`:
  - `Duplicated key [Charlie] found on index 'Employee%257EPerson_...'`
- `26.3.2`:
  - `Record #5:2 not found`
- `26.4.1-SNAPSHOT`:
  - `Record #1:2 not found`
- `26.4.2`:
  - `Record #1:2 not found`

So the repeated label application fails instead of acting idempotently.

**Control cases**

If the label is applied only once per node, ArcadeDB behaves normally:

```cypher
MATCH (n:Person)
WHERE n.age > 25
SET n:Employee
RETURN n.name AS name
ORDER BY name;
```

Observed result on Neo4j and all tested ArcadeDB versions:

```text
Alice
Charlie
```

If the duplicated rows are collapsed first, ArcadeDB also behaves normally:

```cypher
MATCH (n:Person)
MATCH (m:Person)
WHERE n.age > m.age
WITH DISTINCT n
SET n:Employee
RETURN n.name AS name
ORDER BY name;
```

Observed result on Neo4j and all tested ArcadeDB versions:

```text
Alice
Charlie
```

So the problem is not ordinary `SET n:Employee`.
It starts when the same bound node is reached multiple times in one statement.

If the same row-fanout shape performs a simple constant property assignment instead of adding a label, ArcadeDB does not fail:

```cypher
MATCH (n:Person)
MATCH (m:Person)
WHERE n.age > m.age
SET n.flag = true
RETURN n.name AS name
ORDER BY name;
```

Observed result on Neo4j and all tested ArcadeDB versions:

```text
Alice
Charlie
Charlie
```

This makes the boundary much clearer:

- repeated row hits alone are fine
- simple constant property assignment on repeated row hits is fine
- repeated `SET n:Label` on the same node is what breaks

Later rechecking on `2026-04-25` found a separate family where repeated self-referential arithmetic updates such as `SET n.age = n.age + 1` do not crash, but also do not accumulate correctly.
That is tracked separately and is not the same bug as this repeated-label failure.

**Stronger reproducer**
The same family also appears when more than one label is added:

```cypher
MATCH (n:Person)
MATCH (m:Person)
WHERE n.age > m.age
SET n:Employee:Checked
RETURN n.name AS name
ORDER BY name;
```

Observed ArcadeDB results:

- `latest`:
  - duplicate-key failure on a multi-label index like `Checked%7EEmployee%7EPerson_...`
- `26.3.2` / `26.4.1-SNAPSHOT` / `26.4.2`:
  - `Record #... not found`

So this does not look specific to the single label name `Employee`.

**Original differential seed**
This family was first surfaced while minimizing:

- `ArcadeData__arcadedb__3253__q001`

Original generated query:

```cypher
MATCH (n)
MATCH (m)
WHERE n.age > m.age
SET n:Employee
MERGE (p:Person {name: 'David', age: 28})
RETURN n, COUNT(*) AS total
```

Direct reduction shows the `MERGE` is not the core issue.
The failure is already present in the smaller `SET n:Employee` reproducer above.

**Verification environment**
Verified directly against:

- Neo4j Docker container: `neo4j:latest`
- ArcadeDB Docker container: `arcadedata/arcadedb:latest`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.3.2`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.4.1-SNAPSHOT`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.4.2`
- Local differential-testing harness
- Direct ArcadeDB HTTP execution using the same request mode as Studio
