## ArcadeDB version
done
Observed on Docker images:

- `arcadedata/arcadedb:latest`
- `arcadedata/arcadedb:26.3.2`

## Environment
- Docker on Windows host
- ArcadeDB queried through the HTTP API: `/api/v1/command/arcade`
- Requests sent in the same shape used by ArcadeDB Studio:
  - `language: opencypher`
  - `serializer: studio`

## Describe the bug
A standalone `ORDER BY ... LIMIT ...` sequence before a following `MATCH` may fail to preserve the selected row.

In the minimized repro below, `ORDER BY o.orderDate DESC LIMIT 1` should keep only the most recent order, `ORD-003`.

Neo4j does that correctly.
ArcadeDB instead continues with `ORD-001`, as if the descending selection had not been preserved when the next `MATCH` clause runs.

## To Reproduce

### Setup
```cypher
CREATE (:Order {id: 'ORD-001', orderDate: 20230101}),
       (:Order {id: 'ORD-002', orderDate: 20230102}),
       (:Order {id: 'ORD-003', orderDate: 20230103}),
       (:Item {name: 'ItemA'}),
       (:Item {name: 'ItemB'}),
       (:Item {name: 'ItemC'});

MATCH (o:Order {id:'ORD-001'}), (i:Item {name:'ItemA'})
CREATE (o)-[:CONTAINS]->(i);

MATCH (o:Order {id:'ORD-002'}), (i:Item {name:'ItemB'})
CREATE (o)-[:CONTAINS]->(i);

MATCH (o:Order {id:'ORD-003'}), (i:Item {name:'ItemC'})
CREATE (o)-[:CONTAINS]->(i);
```

### Query
```cypher
MATCH (o:Order)
ORDER BY o.orderDate DESC
LIMIT 1
MATCH (o)-[:CONTAINS]->(i:Item)
RETURN o.id AS orderId, collect(i.name) AS items;
```

## Expected behavior
The descending sort and `LIMIT 1` should keep only the newest order:

```text
orderId = ORD-003
items = ["ItemC"]
```

## Actual behavior
ArcadeDB returns the oldest order instead:

```text
orderId = ORD-001
items = ["ItemA"]
```

## Control cases
Control 1, the standalone selection itself works if returned immediately:

```cypher
MATCH (o:Order)
ORDER BY o.orderDate DESC
LIMIT 1
RETURN o.id AS orderId;
```

Observed result on both Neo4j and ArcadeDB:

```text
ORD-003
```

Control 2, inserting an explicit `WITH` also behaves correctly on both engines:

```cypher
MATCH (o:Order)
WITH o
ORDER BY o.orderDate DESC
LIMIT 1
MATCH (o)-[:CONTAINS]->(i:Item)
RETURN o.id AS orderId, collect(i.name) AS items;
```

Observed result on both Neo4j and ArcadeDB:

```text
orderId = ORD-003
items = ["ItemC"]
```

Control 3, ascending order on the original query returns the first order as expected:

```cypher
MATCH (o:Order)
ORDER BY o.orderDate ASC
LIMIT 1
MATCH (o)-[:CONTAINS]->(i:Item)
RETURN o.id AS orderId, collect(i.name) AS items;
```

Observed result on both Neo4j and ArcadeDB:

```text
orderId = ORD-001
items = ["ItemA"]
```

This suggests the problem is specifically tied to preserving a descending standalone `ORDER BY ... LIMIT` selection across the following `MATCH`, rather than to sorting or limiting in general.

## Additional context
Differential testing found other queries in the same family where:

- Neo4j respects a top-`N` row restriction before a later `MATCH` or `OPTIONAL MATCH`
- ArcadeDB continues from a different row than the selected one

So this appears broader than a single `Order` dataset.

## Verification environment
Verified directly against:

- Neo4j Docker container: `neo4j:latest`
- ArcadeDB Docker container: `arcadedata/arcadedb:latest`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.3.2`
- Local differential-testing harness
- Direct ArcadeDB HTTP execution using the same request mode as Studio

