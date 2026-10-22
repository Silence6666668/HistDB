done
**Suggested title**
`Correlated COLLECT { ... } subqueries with outer-variable access may evaluate as empty lists`

**ArcadeDB version**
Observed on Docker images:

- `arcadedata/arcadedb:26.4.1-SNAPSHOT`
- `arcadedata/arcadedb:26.4.2`

Older lines also behave incorrectly, but differently:

- `arcadedata/arcadedb:latest`
- `arcadedata/arcadedb:26.3.2`

On those older lines, the same correlated form currently returns `null` instead of a list.
On the `26.4.x` line, the subquery is accepted and typed as a list, but the list is incorrectly empty.

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
On ArcadeDB `26.4.1-SNAPSHOT` and `26.4.2`, a correlated `COLLECT { ... }` subquery may evaluate as an empty list when the inner body reads an outer variable.

In the minimized repro below, the inner subquery clearly produces rows:

- `Alice` should collect `Charlie` and `Diana`
- `Charlie` should collect `Diana`
- `Diana` should collect nothing

Neo4j returns the expected lists.
ArcadeDB instead returns `[]` for every row.

This does not look like a general `COLLECT { ... }` problem:

- uncorrelated `COLLECT { MATCH ... }` works
- the same logic expressed with ordinary `OPTIONAL MATCH + collect(...)` also works

So the boundary appears to be correlated outer-variable access inside the `COLLECT` body.

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
       COLLECT {
         MATCH (p2:Person)
         WHERE p2.age > p.age
         RETURN p2.name
       } AS xs
ORDER BY name;
```

**Expected behavior**
Observed Neo4j result:

```text
Alice,   ['Charlie', 'Diana']
Charlie, ['Diana']
Diana,   []
```

**Actual behavior**
Observed ArcadeDB `26.4.1-SNAPSHOT` / `26.4.2` result:

```text
Alice,   []
Charlie, []
Diana,   []
```

Observed ArcadeDB `latest` / `26.3.2` result:

```text
Alice,   null
Charlie, null
Diana,   null
```

So the correlated `COLLECT { ... }` does not preserve the rows produced by the inner comparison.

**Control cases**

The same `COLLECT { ... }` structure works when the inner subquery is uncorrelated:

```cypher
MATCH (p:Person)
WHERE p.age > 25
RETURN p.name AS name,
       COLLECT {
         MATCH (p2:Person)
         WHERE p2.age > 30
         RETURN p2.name
       } AS xs
ORDER BY name;
```

Observed result on Neo4j and ArcadeDB `26.4.1-SNAPSHOT` / `26.4.2`:

```text
Alice,   ['Charlie', 'Diana']
Charlie, ['Charlie', 'Diana']
Diana,   ['Charlie', 'Diana']
```

So `COLLECT { MATCH ... }` itself is fine.

The same logical test also works when expressed without `COLLECT { ... }`:

```cypher
MATCH (p:Person)
WHERE p.age > 25
OPTIONAL MATCH (p2:Person)
WHERE p2.age > p.age
RETURN p.name AS name,
       collect(p2.name) AS xs
ORDER BY name;
```

Observed result on Neo4j and ArcadeDB `26.4.1-SNAPSHOT` / `26.4.2`:

```text
Alice,   ['Charlie', 'Diana']
Charlie, ['Diana']
Diana,   []
```

So the data and comparison itself are not the problem.

Aliasing the outer value also does not avoid the bug:

```cypher
MATCH (p:Person)
WHERE p.age > 25
WITH p, p.age AS age
RETURN p.name AS name,
       COLLECT {
         MATCH (p2:Person)
         WHERE p2.age > age
         RETURN p2.name
       } AS xs
ORDER BY name;
```

Observed Neo4j result:

```text
Alice,   ['Charlie', 'Diana']
Charlie, ['Diana']
Diana,   []
```

Observed ArcadeDB `26.4.1-SNAPSHOT` / `26.4.2` result:

```text
Alice,   []
Charlie, []
Diana,   []
```

This makes the boundary clearer:

- uncorrelated `COLLECT`: correct
- equivalent non-`COLLECT` logic: correct
- correlated inner comparison against an outer value: incorrect

At the same time, the following still behaves correctly on both engines:

```cypher
MATCH (p:Person)
RETURN p.name AS name,
       COLLECT {
         OPTIONAL MATCH (p)-[:KNOWS]->(q)
         RETURN q.name
       } AS xs
ORDER BY name;
```

Observed result:

```text
Alice,   ['Bob']
Bob,     [null]
Charlie, [null]
Diana,   [null]
```

So this does not look like all correlated `COLLECT` subqueries fail.
It looks more like outer-variable access modes inside the subquery are mishandled:

- uncorrelated inner match: correct
- correlated `OPTIONAL MATCH` over the same node variable: correct
- scalar comparison against an outer value: incorrect

**Why this is kept separate**
This is related to the older submitted `COLLECT { ... }` family, but the boundary is different.

- the old submitted family returns `null` for correlated collect subqueries more generally on older lines
- this candidate is specifically about correlated outer-variable access on the `26.4.x` line, where the syntax is accepted and a list is returned, but the list is always empty

So this is intentionally kept as a separate family rather than folding it into the older `null`-returning report.

**Differential seed**
This family was uncovered while compacting the same `26.4.x` correlated-subquery lane that also produced the separate `EXISTS { ... }` and `COUNT { ... }` outer-variable candidates.

The issue is already visible in the direct reproducer above; no write clause or larger generated query is needed to trigger it.

**Verification environment**
Verified directly against:

- Neo4j Docker container: `neo4j:latest`
- ArcadeDB Docker container: `arcadedata/arcadedb:latest`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.3.2`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.4.1-SNAPSHOT`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.4.2`
- Local differential-testing harness
- Direct ArcadeDB HTTP execution using the same request mode as Studio
