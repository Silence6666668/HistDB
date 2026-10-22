**Suggested title**
`Correlated COUNT { ... } subqueries with outer-variable access may evaluate as zero`

**ArcadeDB version**
Observed on Docker images:

- `arcadedata/arcadedb:26.4.1-SNAPSHOT`
- `arcadedata/arcadedb:26.4.2`

Older images also behave incorrectly, but differently:

- `arcadedata/arcadedb:latest`
- `arcadedata/arcadedb:26.3.2`

On those older lines, the same correlated form currently returns `null` instead of an integer.
On the `26.4.x` line, the subquery is accepted and typed as an integer, but still evaluates to the wrong value `0`.

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
On ArcadeDB `26.4.1-SNAPSHOT` and `26.4.2`, a correlated `COUNT { ... }` subquery may evaluate as `0` when the inner body compares against an outer variable.

In the minimized repro below, the inner subquery clearly produces rows:

- `Alice` has two older people
- `Charlie` has one older person
- `Diana` has none

Neo4j returns `2`, `1`, and `0`.
ArcadeDB returns `0` for every row.

This does not look like a general `COUNT { MATCH ... }` problem.
If the inner `MATCH` is uncorrelated, ArcadeDB counts correctly.
The failure starts when the inner predicate reads an outer variable such as `p.age`.

**To Reproduce**

Setup:

```cypher
CREATE (:Person {name:'Alice', age:30}),
       (:Person {name:'Bob', age:25}),
       (:Person {name:'Charlie', age:35}),
       (:Person {name:'Diana', age:40});
```

Query:

```cypher
MATCH (p:Person)
WHERE p.age > 25
RETURN p.name AS name,
       COUNT {
         MATCH (p2:Person)
         WHERE p2.age > p.age
         RETURN p2
       } AS older_count
ORDER BY name;
```

**Expected behavior**
Observed Neo4j result:

```text
Alice,   2
Charlie, 1
Diana,   0
```

**Actual behavior**
Observed ArcadeDB `26.4.1-SNAPSHOT` / `26.4.2` result:

```text
Alice,   0
Charlie, 0
Diana,   0
```

Observed ArcadeDB `latest` / `26.3.2` result:

```text
Alice,   null
Charlie, null
Diana,   null
```

So the correlated `COUNT { ... }` does not count the matching rows correctly.

**Control cases**

The same logic written with ordinary aggregation behaves correctly:

```cypher
MATCH (p:Person)
WHERE p.age > 25
OPTIONAL MATCH (p2:Person)
WHERE p2.age > p.age
RETURN p.name AS name, count(p2) AS older_count
ORDER BY name;
```

Observed result on Neo4j and all tested ArcadeDB versions:

```text
Alice,   2
Charlie, 1
Diana,   0
```

So the problem is not with the comparison `p2.age > p.age` itself.
It is specific to the correlated `COUNT { ... }` form.

If the `COUNT { ... }` body is left uncorrelated, ArcadeDB `26.4.1-SNAPSHOT` / `26.4.2` also behaves correctly:

```cypher
MATCH (p:Person)
WHERE p.age > 25
RETURN p.name AS name,
       COUNT {
         MATCH (p2:Person)
         WHERE p2.age > 30
         RETURN p2
       } AS older_count
ORDER BY name;
```

Observed result on Neo4j and ArcadeDB `26.4.1-SNAPSHOT` / `26.4.2`:

```text
Alice,   2
Charlie, 2
Diana,   2
```

This makes the boundary much clearer:

- `COUNT { MATCH ... }` alone can work
- the same comparison works with `OPTIONAL MATCH + count(...)`
- the failure starts when the `COUNT { ... }` body reads an outer variable

**Why this is kept separate**
This is related to ArcadeDB's other `COUNT { ... }` issues, but the boundary is different.

- the old submitted count-pattern family returns `null` or even leaks bound values in some pattern forms
- the newer `COUNT { ... }` body-clause family focuses on `UNWIND`, `CALL`, `OPTIONAL MATCH`, and inner `WITH`
- this candidate is specifically about correlated outer-variable access inside an otherwise ordinary `MATCH` body

So this is intentionally kept as a separate family rather than folding it into the broader body-clause candidate.

**Differential seed**
This family surfaced while minimizing:

- `RedisGraph__RedisGraph__2368__q001`

Original generated query:

```cypher
MATCH (p:Person)
WITH DISTINCT p
WHERE p.age > 25
RETURN p.name,
       COUNT { MATCH (p2:Person) WHERE p2.age > p.age }
```

Direct reduction shows the core issue is the correlated outer-variable access inside `COUNT { ... }`, not `DISTINCT` or ordering.

**Verification environment**
Verified directly against:

- Neo4j Docker container: `neo4j:latest`
- ArcadeDB Docker container: `arcadedata/arcadedb:latest`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.3.2`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.4.1-SNAPSHOT`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.4.2`
- Local differential-testing harness
- Direct ArcadeDB HTTP execution using the same request mode as Studio
