## ArcadeDB version
Observed on Docker images:

- `arcadedata/arcadedb:latest`
- `arcadedata/arcadedb:26.4.1-SNAPSHOT`

## Environment
- Docker on Windows host
- ArcadeDB queried through the HTTP API: `/api/v1/command/arcade`
- Requests sent in the same shape used by ArcadeDB Studio:
  - `language: opencypher`
  - `serializer: studio`

## Describe the bug
If a `CALL` subquery reuses an outer variable name for its own inner match variable, ArcadeDB may drop all rows.

In the repro below, the outer query binds `p` to one row (`Bob`).
The inner subquery also uses the name `p` for an unrelated match over all people with a `city`.

Neo4j returns the expected 3-row cross product:

```text
Bob, Berlin
Bob, London
Bob, Paris
```

ArcadeDB returns no rows at all.

If the inner variable is renamed from `p` to `q`, ArcadeDB behaves correctly.

## To Reproduce

### Setup
```cypher
CREATE (:Person {name:'Bob', age:30, role:'Employee'}),
       (:Person {name:'X', city:'Berlin'}),
       (:Person {name:'Y', city:'Paris'}),
       (:Person {name:'Z', city:'London'});
```

### Query
```cypher
MATCH (p:Person {name:'Bob'})
CALL {
  MATCH (p:Person)
  WHERE p.city IS NOT NULL
  RETURN p.city AS location
}
RETURN p.name AS outerName, location
ORDER BY location;
```

## Expected behavior
The inner `MATCH` should produce three rows, which should combine with the single outer row:

```text
Bob, Berlin
Bob, London
Bob, Paris
```

## Actual behavior
ArcadeDB returns:

```text
(0 rows)
```

## Control cases
Control 1, simply renaming the inner variable makes ArcadeDB behave correctly:

```cypher
MATCH (p:Person {name:'Bob'})
CALL {
  MATCH (q:Person)
  WHERE q.city IS NOT NULL
  RETURN q.city AS location
}
RETURN p.name AS outerName, location
ORDER BY location;
```

Observed result on both Neo4j and ArcadeDB:

```text
Bob, Berlin
Bob, London
Bob, Paris
```

Control 2, the inner subquery by itself also behaves correctly on ArcadeDB:

```cypher
CALL {
  MATCH (p:Person)
  WHERE p.city IS NOT NULL
  RETURN p.city AS location
}
RETURN location
ORDER BY location;
```

Observed result on both Neo4j and ArcadeDB:

```text
Berlin
London
Paris
```

This suggests the issue is specifically tied to reusing an outer variable name inside the subquery, not to the inner query itself.

## Verification environment
Verified directly against:

- Neo4j Docker container: `neo4j:latest`
- ArcadeDB Docker container: `arcadedata/arcadedb:latest`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.3.2`
- Local differential-testing harness
- Direct ArcadeDB HTTP execution using the same request mode as Studio

