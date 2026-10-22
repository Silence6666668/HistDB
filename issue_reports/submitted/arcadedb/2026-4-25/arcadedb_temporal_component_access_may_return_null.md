**Suggested title**
`Temporal component access on date/datetime values may return null`

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
ArcadeDB may return `null` when reading standard temporal components from `date(...)` or `datetime(...)` values, even though the temporal values themselves are created correctly.

In the minimized repros below:

- `date()` itself returns a valid date string
- but `date().year`, `date().month`, `date().day`, and `datetime().year` all evaluate to `null`

Neo4j returns the expected numeric components.

**To Reproduce**

Query:

```cypher
RETURN date().year AS y;
```

**Expected behavior**
Neo4j returns the current year as an integer.

On `2026-04-25`, observed Neo4j result:

```text
2026
```

ArcadeDB should also return the current year.

**Actual behavior**
ArcadeDB returns:

```text
null
```

**Control query 1**
The temporal value itself is created correctly:

```cypher
RETURN date() AS d;
```

Observed result on ArcadeDB:

```text
2026-04-25
```

So the failure is not that `date()` itself is unavailable.

**Control query 2**
The issue is not limited to `date()` without arguments:

```cypher
RETURN date('2020-01-15').year AS y;
```

Observed Neo4j result:

```text
2020
```

Observed ArcadeDB result:

```text
null
```

**Control query 3**
The same family also affects other date and datetime components:

```cypher
RETURN date().month AS m, date().day AS d;
```

Observed Neo4j result on `2026-04-25`:

```text
4, 25
```

Observed ArcadeDB result:

```text
null, null
```

And:

```cypher
RETURN datetime().year AS y;
```

Observed Neo4j result:

```text
2026
```

Observed ArcadeDB result:

```text
null
```

This makes the boundary clear: ArcadeDB can produce temporal values, but component access on those values is being evaluated incorrectly.

**Differential seed**
This family surfaced while minimizing:

- `RedisGraph__RedisGraph__416__q004`

That larger query produced `null` relationship properties for:

```cypher
date().year - person.age + 18
```

After minimization, the core issue turned out to be the temporal component access itself.

**Verification environment**
Verified directly against:

- Neo4j Docker container: `neo4j:latest`
- ArcadeDB Docker containers:
  - `arcadedata/arcadedb:latest`
  - `arcadedata/arcadedb:26.3.2`
  - `arcadedata/arcadedb:26.4.1-SNAPSHOT`
  - `arcadedata/arcadedb:26.4.2`
- ArcadeDB request mode aligned with Studio (`opencypher` + `serializer: studio`)

