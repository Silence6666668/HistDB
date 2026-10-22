done
**Suggested title**
`Relationship created earlier in a query may be invisible to a later MATCH in the same query`

**FalkorDB version**
Observed on:

- FalkorDB `4.18.1`
- FalkorDB `4.18.2`
- FalkorDB `4.18.3`

Docker image used locally:
- `falkordb/falkordb:latest`
- `falkordb/falkordb:v4.18.2`
- `falkordb/falkordb:v4.18.3`

**Discovered on**
- `2026-04-23`

**Environment**
- Host OS: Windows
- Architecture: x86_64
- Deployment: Docker
- Connection method: RESP / `GRAPH.QUERY`
- Client used for direct reproduction: project FalkorDB executor
- Differential comparison target: Neo4j Community `2026.02.2`

**Describe the bug**
FalkorDB may fail to read a relationship that was just created earlier in the same query.

In the minimized repro below, the query:

1. creates two nodes
2. creates a `[:KNOWS]` relationship between them
3. carries the bound nodes through `WITH`
4. immediately matches the same relationship again

Neo4j returns the created relationship as expected.
FalkorDB returns no rows.

This suggests that, in FalkorDB, relationships created earlier in a query can be invisible to a later `MATCH` in that same query, even though the relationship is already visible to direct projection.

**To Reproduce**
1. Start the FalkorDB Docker container.
2. Run:

```cypher
CREATE (a:Person {name:'Alice'})
CREATE (b:Person {name:'Bob'})
CREATE (a)-[:KNOWS {since:2020}]->(b)
WITH a, b
MATCH (a)-[r:KNOWS]->(b)
RETURN r.since AS since;
```

**Expected behavior**
Neo4j returns:

```text
2020
```

FalkorDB should return the same row.

**Actual behavior**
FalkorDB returns:

```text
0 rows
```

**Control query 1**
Creating a node and matching the node later in the same query works on both engines:

```cypher
CREATE (a:Person {name:'Alice'})
WITH a
MATCH (a:Person {name:'Alice'})
RETURN a.name AS name;
```

Observed result on both Neo4j and FalkorDB:

```text
Alice
```

So read-after-write is not broken in general for node visibility.

**Control query 2**
Returning the relationship directly right after `CREATE` also works on both engines:

```cypher
CREATE (a:Person {name:'Alice'})
CREATE (b:Person {name:'Bob'})
CREATE (a)-[r:KNOWS {since:2020}]->(b)
RETURN r.since AS since;
```

Observed result on both Neo4j and FalkorDB:

```text
2020
```

So the failure is not in creating or projecting the relationship itself.

**Stronger reproducer**
The same issue appears on a slightly larger pattern:

```cypher
CREATE (a:Person {name: 'Alice', age: 30})
CREATE (b:Person {name: 'Bob', age: 25})
CREATE (c:Person {name: 'Charlie', age: 35})
CREATE (a)-[:KNOWS {since: 2020}]->(b)
CREATE (b)-[:KNOWS {since: 2021}]->(c)
CREATE (c)-[:KNOWS {since: 2022}]->(a)
WITH a, b, c
MATCH (a)-[r1:KNOWS]->(b)-[r2:KNOWS]->(c)-[r3:KNOWS]->(a)
RETURN a.name AS startName,
       b.name AS middleName,
       c.name AS endName,
       r1.since AS year1,
       r2.since AS year2,
       r3.since AS year3;
```

Observed Neo4j result:

```text
Alice, Bob, Charlie, 2020, 2021, 2022
```

Observed FalkorDB result:

```text
0 rows
```

This makes the boundary clearer: the problem is tied to a later `MATCH` over relationships created earlier in the same query, not to projection of created entities in general.

**Additional supporting reproducer**
The same family also shows up when the later clause is an `OPTIONAL MATCH` plus aggregation rather than a hard `MATCH`:

```cypher
MATCH (p:Person {name: 'Alice'})
MATCH (c:Company {name: 'TechCorp'})
CREATE (p)-[r:WORKS_AT {since: 2020}]->(c)
WITH p, r, c
OPTIONAL MATCH (p)-[other:WORKS_AT]->(c2:Company)
WHERE other.since > 2018
RETURN p.name AS person_name,
       type(r) AS relationship_type,
       c.name AS company_name,
       count(other) AS other_relationships;
```

Observed Neo4j result:

```text
Alice, WORKS_AT, TechCorp, 1
```

Observed FalkorDB result:

```text
Alice, WORKS_AT, TechCorp, 0
```

So the issue is not limited to a later hard `MATCH` returning `0 rows`; it can also surface as a wrong aggregate count when a later optional traversal should still see the newly created relationship.

**Additional supporting reproducer 2**
The same issue also reproduces when the relationship is introduced through `MERGE` and a later `MATCH` is followed by another write:

```cypher
CREATE (:Person {name:'Alice', age:30});
CREATE (:Person {name:'Bob', age:25});
CREATE (:Person {name:'Charlie', age:35});

MATCH (a:Person {name: 'Alice'}), (b:Person {name: 'Bob'})
MERGE (a)-[r:KNOWS]->(b)
  ON CREATE SET r.since = 2024
  ON MATCH SET r.since = 2023
CREATE (c:Person {name: 'Charlie'})
WITH a, b, c
MATCH (a)-[r1:KNOWS]->(b)
CREATE (c)-[r2:FRIENDS_WITH]->(a)
RETURN r1.since AS relationship_year,
       r2.type AS new_relationship_type,
       'Query completed' AS status;
```

Observed Neo4j result:

```text
2024, null, Query completed
```

Observed FalkorDB result:

```text
0 rows
```

So the family is not limited to plain `CREATE`; a relationship introduced by `MERGE` can also become invisible to a later `MATCH` in the same query.

**Additional supporting reproducer 3**
The same invisibility also appears on self-loops and path matching:

```cypher
CREATE (:Person {name:'Alice', age:30}),
       (:Person {name:'Bob', age:25}),
       (:Person {name:'Charlie', age:35});

MATCH (p:Person)
WITH p, p.age AS ageVal
CREATE (p)-[:KNOWS {since: 2020}]->(p)
SET p.updated = true
WITH p, ageVal
MATCH path = (p)-[:KNOWS*1]->(p)
RETURN p.name, avg(ageVal) AS avgAge, count(path) AS selfRefCount;
```

Observed Neo4j result:

```text
Alice,   30.0, 1
Bob,     25.0, 1
Charlie, 35.0, 1
```

Observed FalkorDB result:

```text
0 rows
```

So the issue is not limited to simple one-hop re-match. It also affects later path matching over relationships that were just created earlier in the same query.

**Additional supporting reproducer 4**
The same issue also appears on a self-loop when the later clause directly re-matches the just-created relationship:

```cypher
CREATE (p1:Person {name: 'Alice', tags: ['tag1', 'tag2']}),
       (p2:Person {name: 'Bob', tags: ['tag3']}),
       (p3:Person {name: 'Charlie', tags: ['tag4', 'tag5']})

MATCH (p:Person {name: 'Alice'})
CREATE (p)-[:FRIEND_OF]->(p)
WITH p
MATCH (p)-[r:FRIEND_OF]->(p)
RETURN p.name AS name,
       p.tags[0] AS first_tag,
       type(r) AS relationship_type;
```

Observed Neo4j result:

```text
Alice, tag1, FRIEND_OF
```

Observed FalkorDB result:

```text
0 rows
```

So the family is not limited to later path matching over self-loops. A direct later one-hop re-match over the same just-created self-loop can also disappear entirely.

**Additional supporting reproducer 5**
The same invisibility also appears when the relationship is created after a node `MERGE`, and the later clause is an `OPTIONAL MATCH` that should bind the same edge:

```cypher
CREATE (c:Person {name: 'Alice'})
CREATE (d:Person {name: 'Bob'})
CREATE (e:Person {name: 'Charlie'})

MATCH (c:Person {name: 'Alice'})
MERGE (e:Employee {name: 'David'})
  ON CREATE SET e.created = true
  ON MATCH SET e.updated = true
CREATE (e)-[:WORKS_FOR]->(c)
WITH e, c
OPTIONAL MATCH (e)-[r:WORKS_FOR]->(c)
RETURN e, c, r;
```

Observed Neo4j result:

```text
e = (:Employee {name: "David", created: TRUE})
c = (:Person {name: "Alice"})
r = [:WORKS_FOR]
```

Observed FalkorDB result:

```text
e = {name: David, created: true}
c = {name: Alice}
r = null
```

So the family is not limited to plain node `CREATE` before the disappearing relationship. A relationship created after `MERGE` and `ON CREATE SET` can still be invisible to a later `OPTIONAL MATCH` in the same query.

**Differential seed**
This family was first noticed in:

- `neo4j__13317__q001`

Related supporting seed from the same rerun:

- `FalkorDB__FalkorDB__461__q002`
- `RedisGraph__RedisGraph__2208__q005`
- `ArcadeData__arcadedb__3446__q001`
- `FalkorDB__FalkorDB__1040__q002`
- `ArcadeData__arcadedb__3711__q001`
- `FalkorDB__FalkorDB__86__q001`
- `RedisGraph__RedisGraph__185__q001`
- `RedisGraph__RedisGraph__2119__q003`
- `RedisGraph__RedisGraph__249__q003`
- `neo4j__12862__q003`
- `RedisGraph__RedisGraph__3216__q001`
- `FalkorDB__FalkorDB__180__q001`
- `RedisGraph__RedisGraph__2230__q002`
- `RedisGraph__RedisGraph__2744__q005`
- `RedisGraph__RedisGraph__1612__q008`
- `RedisGraph__RedisGraph__1000__q004`
- `RedisGraph__RedisGraph__1254__q010`
- `RedisGraph__RedisGraph__361__q008`
- `RedisGraph__RedisGraph__521__q015`
- `RedisGraph__RedisGraph__628__q003`

**Additional supporting reproducer 6**
The same invisibility also appears when the later clause tries to delete one of several just-created relationships:

```cypher
CREATE (:Person {name:'Alice'}),
       (:Person {name:'Bob'}),
       (:Person {name:'Charlie'});

MATCH (a:Person {name:'Alice'}), (b:Person {name:'Bob'}), (c:Person {name:'Charlie'})
CREATE (a)-[:FRIEND]->(b),
       (a)-[:FRIEND]->(c),
       (a)-[:KNOWS]->(b),
       (a)-[:KNOWS]->(c)
WITH a, b, c
MATCH (a)-[r:FRIEND]->(b)
DELETE r
RETURN a.name AS a, b.name AS b, c.name AS c;
```

Observed Neo4j result:

```text
Alice, Bob, Charlie
```

Observed FalkorDB result:

```text
0 rows
```

So the family is not limited to later read-only `MATCH`. Even a later `MATCH ... DELETE r` can fail because the just-created relationship is not visible to that later clause.

**Additional supporting reproducer 7**
The same issue also appears when a created relationship is later deleted and recreated in the same query:

```cypher
CREATE (:Person {name:'Alice'}),
       (:Person {name:'Bob'});

MATCH (p1:Person {name:'Alice'}), (p2:Person {name:'Bob'})
CREATE (p1)-[r:KNOWS]->(p2)
SET r = {strength: 5}
WITH p1, p2
MATCH (p1)-[old:KNOWS]->(p2)
DELETE old
WITH p1, p2
MERGE (p1)-[new:KNOWS]->(p2)
RETURN p1.name AS p1, p2.name AS p2, type(new) AS relType;
```

Observed Neo4j result:

```text
Alice, Bob, KNOWS
```

Observed FalkorDB result:

```text
0 rows
```

So the family is not limited to a single later re-match. Once the query needs to see the just-created relationship in an intermediate later `MATCH`, FalkorDB can drop the entire row and prevent the delete-and-recreate flow from completing.

**Additional supporting reproducer 8**
The same invisibility also appears when two different relationship types are created and later rebound through explicit path variables:

```cypher
CREATE (:Person {name:'Alice'}),
       (:Person {name:'Bob'});

MATCH (p1:Person {name:'Alice'}), (p2:Person {name:'Bob'})
CREATE (p1)-[r1:KNOWS]->(p2),
       (p1)-[r2:LIKES]->(p2)
WITH p1, p2, r1, r2
MATCH path1 = (p1)-[:KNOWS]->(p2),
      path2 = (p1)-[:LIKES]->(p2)
SET p1 += {age: 31, city: 'New York'},
    p2 += {age: 26, city: 'Boston'}
RETURN p1.name AS p1,
       p2.name AS p2,
       type(r1) AS r1type,
       type(r2) AS r2type,
       length(path1) AS l1,
       length(path2) AS l2;
```

Observed Neo4j result:

```text
Alice, Bob, KNOWS, LIKES, 1, 1
```

Observed FalkorDB result:

```text
0 rows
```

So the family is not limited to one relationship type or one later relationship binding. Later path matching over multiple just-created edges can disappear entirely as well.

**Additional supporting reproducer 9**
The same invisibility also appears when a fresh node and relationship are created together and the later clause tries to re-match that relationship:

```cypher
CREATE (c:Person {name: 'Alice'});

MATCH (c {name: 'Alice'})
CREATE (e:Employee {name: 'Bob', id: 1})-[:WORKS_FOR]->(c)
WITH e, c
MATCH (e)-[r:WORKS_FOR]->(c)
RETURN e.name AS employee_name,
       c.name AS manager_name,
       type(r) AS relationship_type;
```

Observed Neo4j result:

```text
Bob, Alice, WORKS_FOR
```

Observed FalkorDB result:

```text
0 rows
```

So the family is not limited to re-matching a relationship between already-existing nodes. A relationship created together with a fresh inline node can also disappear from a later `MATCH` in the same query.

**Additional supporting reproducer 10**
The same invisibility can also surface as a later aggregate returning `0` / `null` instead of a missing row:

```cypher
CREATE (:Person {name:'Alice'}),
       (:Company {name:'TechCorp'});

MATCH (p:Person {name: 'Alice'})
MATCH (c:Company {name: 'TechCorp'})
CREATE (p)-[:WORKS_AT {since: 2020}]->(c)
WITH p, c
MATCH (p)-[r:WORKS_AT]->(c)
RETURN collect(r.since)[0] AS first_year,
       count(*) AS total_relationships
LIMIT 1;
```

Observed Neo4j result:

```text
2020, 1
```

Observed FalkorDB result:

```text
null, 0
```

So the family is not only about later `MATCH` returning `0 rows`. FalkorDB can also keep the row alive but behave as if the just-created relationship does not exist.

**Additional supporting reproducer 11**
The same issue also appears when the later clause aggregates relationship metadata into a collected map:

```cypher
CREATE (:Person {name:'Alice', age:30}),
       (:Person {name:'Bob', age:25});

CREATE (a:Person {name: 'Alice', age: 30}),
       (b:Person {name: 'Bob', age: 25})
WITH a, b
CREATE (a)-[:KNOWS]->(b)
WITH a, b
MATCH (p:Person)-[r:KNOWS]->(q:Person)
WHERE p.age > 25
WITH p, q, r, count(r) AS relCount, sum(p.age) AS totalAge
RETURN collect({
  person: p.name,
  friend: q.name,
  relationship: type(r),
  count: relCount,
  sumAge: totalAge
}) AS relationships;
```

Observed Neo4j result:

```text
[{person: Alice, friend: Bob, relationship: KNOWS, count: 1, sumAge: 30}]
```

Observed FalkorDB result:

```text
[{person: Alice, friend: Bob, relationship: null, count: 0, sumAge: 30}]
```

So the family can also corrupt later relationship-derived scalars such as `type(r)` and `count(r)`, not just later row survival.

**Additional supporting reproducer 12**
The same invisibility also appears when the relationship is introduced by `MERGE` to a freshly merged inline node pattern:

```cypher
MERGE (p:Person {name: 'Alice'})
  ON CREATE SET p.age = 30, p.city = 'New York'
MERGE (p)-[:KNOWS]->(q:Person:Employee {name: 'Bob'})
  ON CREATE SET q.salary = 50000, q.department = 'Sales'
WITH p, q
MATCH (p)-[r:KNOWS]->(q)
RETURN p.name AS person1,
       q.name AS person2,
       type(r) AS relType;
```

Observed Neo4j result:

```text
Alice, Bob, KNOWS
```

Observed FalkorDB result:

```text
0 rows
```

So the family is not limited to relationships created between already matched nodes. A relationship introduced together with a freshly merged inline node can also disappear from a later `MATCH`.

**Additional supporting reproducer 13**
The same issue also appears when the later clause re-matches the just-created relationship under a property filter and then performs another write:

```cypher
CREATE (:Person {name:'Alice'}),
       (:Company {name:'TechCorp'});

MATCH (p:Person {name: 'Alice'}), (c:Company {name: 'TechCorp'})
MERGE (p)-[r:WORKS_AT]->(c)
  ON CREATE SET r.since = 2023
WITH p, c, r
MATCH (p)-[r2:WORKS_AT]->(c)
WHERE r2.since = 2023
MERGE (c)-[:HAS_EMPLOYEE]->(p)
RETURN p.name, c.name, r.since;
```

Observed Neo4j result:

```text
Alice, TechCorp, 2023
```

Observed FalkorDB result:

```text
0 rows
```

So the family is not limited to a plain later re-match. The relationship can also disappear when the later clause needs to re-bind it under a property predicate before continuing into another write.

**Additional supporting reproducer 14**
The same invisibility also appears when the just-created relationship is a self-loop introduced by `MERGE`:

```cypher
MATCH (p:Person)
WHERE p.name = 'Alice'
MERGE (p)-[:FRIEND]->(p)
WITH p
MATCH (p)-[r:FRIEND]->(p)
RETURN p.name AS name, COUNT(r) AS self_friends;
```

Observed Neo4j result:

```text
Alice, 1
```

Observed FalkorDB result:

```text
0 rows
```

So the family is not limited to relationships between different endpoints. A self-loop created earlier in the same query can also disappear from a later `MATCH`.

**Additional supporting reproducer 15**
The same issue also appears when the later clause re-binds the just-created relationship under a property predicate, aggregates over it, and then uses that aggregate in a later `MERGE`:

```cypher
CREATE (p:Person {name: 'Alice', age: 30});
CREATE (c:Company {name: 'TechCorp', revenue: 1000000});

MATCH (p:Person {name: 'Alice'}), (c:Company {name: 'TechCorp'})
WITH p, c
MERGE (p)-[r:WORKS_AT {since: 2020}]->(c)
WITH p, c, r
MATCH (p)-[r2:WORKS_AT]->(c)
WHERE r2.since = 2020
WITH p, c, r2, min(r2.since) AS earliestYear
MERGE (p)-[r3:WORKS_AT {since: earliestYear}]->(c)
RETURN p.name, c.name, earliestYear;
```

Observed Neo4j result:

```text
Alice, TechCorp, 2020
```

Observed FalkorDB result:

```text
0 rows
```

So the family is not limited to a plain later re-match. The same invisibility also appears when the later clause needs to re-bind the relationship under a property filter, compute an aggregate from it, and feed that aggregate into a second relationship `MERGE`.

**Additional supporting reproducer 16**
The same invisibility can also surface as a later `OPTIONAL MATCH` keeping the row but returning a `null` relationship property instead of the just-created value:

```cypher
CREATE (a:Person {name: 'Alice'}),
       (b:Person {name: 'Bob'})
WITH a, b
CREATE (a)-[:KNOWS {since: 2020}]->(b)
WITH a, b
OPTIONAL MATCH (a)-[r:KNOWS]->(b)
RETURN a.name AS person1,
       b.name AS person2,
       r.since AS known_since;
```

Observed Neo4j result:

```text
Alice, Bob, 2020
```

Observed FalkorDB result:

```text
Alice, Bob, null
```

So the family is not only about later `MATCH` returning `0 rows`. FalkorDB can also preserve the row while behaving as if the just-created relationship were absent from the later optional traversal.

**Additional supporting reproducer 17**
The same issue also appears when the later clause re-matches the just-merged relationship only to delete it:

```cypher
MATCH (p:Person {name: 'Alice'})
MERGE (p)-[r:WORKS_AT {since: 2020}]->(c:Company {name: 'TechCorp'})
WITH p, r, c
MATCH (p)-[r2:WORKS_AT]->(c)
DELETE r2
RETURN p.name AS personName,
       c.industry AS companyIndustry,
       'Relationship deleted' AS status;
```

Observed Neo4j result:

```text
Alice, null, Relationship deleted
```

Observed FalkorDB result:

```text
0 rows
```

So the family is not limited to later read-only re-matches or later aggregates. A just-created relationship can also disappear from a later `MATCH` whose only purpose is to delete that same edge.

**Additional supporting reproducer 18**
The same invisibility also appears when the later clause re-matches the just-created relationship, updates node properties, and then deletes the relationship:

```cypher
MATCH (c {name: 'Alice'})
CREATE (e:Employee {name: 'Bob', skills: [skill IN ['Cypher', 'Python', 'Java'] | skill]})-[:WORKS_FOR]->(c)
WITH e, c
MATCH (e)-[r:WORKS_FOR]->(c)
SET e.department = NULL, c.manager = e.name
DELETE r
RETURN e, c;
```

Observed Neo4j result:

```text
e = {name: Bob, skills: [Cypher, Python, Java]}
c = {name: Alice, age: 30, manager: Bob}
```

Observed FalkorDB result:

```text
0 rows
```

So the family is not limited to later re-matches that only read, aggregate, or delete. A just-created relationship can also disappear when the later clause first updates carried node values and then deletes that same edge.

**Additional supporting reproducer 19**
The same current-version family also appears when the later clause updates a carried node with a map replacement before re-matching the just-created relationship:

```cypher
CREATE (a:Person {name: 'Alice'}), (b:Person {name: 'Bob'}), (c:Person {name: 'Charlie'})
WITH a, b, c
MATCH (x:Person), (y:Person)
WHERE x.name = 'Alice' AND y.name = 'Bob'
CREATE (x)-[:KNOWS]->(y)
WITH x, y, c
SET c = {age: 30, city: 'NYC'}
WITH x, y, c
MATCH p = (x)-[:KNOWS]->(y)
RETURN count(p) AS pathCount
LIMIT 1;
```

Observed results:

- Neo4j:

```text
1
```

- FalkorDB `4.18.2`:

```text
0
```

So the current-version invisibility is not limited to later plain reads. It also survives an intervening map-style node update before the later re-match.

**Additional supporting reproducer 20**
The same current-version family also appears in a smaller `MERGE ... ON CREATE SET` shape where the later clause simply re-matches the just-merged relationship:

```cypher
MATCH (a:Person {name: 'Alice'}), (b:Person {name: 'Bob'})
MERGE (a)-[r:KNOWS]->(b)
  ON CREATE SET r.since = 2020
WITH a, b, r
MATCH (a)-[r2:KNOWS]->(b)
RETURN a.name, b.name, r.since;
```

Observed results:

- Neo4j:

```text
Alice, Bob, 2020
```

- FalkorDB `4.18.3`:

```text
0 rows
```

So the family is not limited to larger write pipelines. Even a simple `MERGE` with `ON CREATE SET` can produce a relationship that immediately disappears from a later `MATCH` in the same query.

**Additional supporting reproducer 21**
The same invisibility also appears across `UNWIND` rows when each row creates a relationship and the later clause tries to re-bind it under a property predicate:

```cypher
UNWIND range(1, 3) AS i
MATCH (a:Person {name: 'Alice'}), (b:Person {name: 'Bob'})
CREATE (a)-[:KNOWS {since: i}]->(b)
WITH a, b, i
MATCH (a)-[r:KNOWS]->(b)
WHERE r.since = i
RETURN a.name, b.name, r.since
ORDER BY r.since;
```

Observed results:

- Neo4j:

```text
Alice, Bob, 1
Alice, Bob, 2
Alice, Bob, 3
```

- FalkorDB `4.18.3`:

```text
0 rows
```

So the family is not limited to a single just-created relationship. Relationships created on successive upstream rows can all become invisible to the later re-match.

**Additional supporting reproducer 22**
The same current-version family also appears when the later clause uses an undirected re-match instead of a directed one:

```cypher
MATCH (a:Person {name: 'Alice'}), (b:Person {name: 'Bob'})
CREATE (a)-[:KNOWS]->(b)
WITH a, b
MATCH (a)-[r:KNOWS]-(b)
RETURN count(r) AS relCount;
```

Observed results:

- Neo4j:

```text
1
```

- FalkorDB `4.18.3`:

```text
0
```

So the family is not limited to later directed re-matches. Even an undirected later `MATCH` can behave as if the just-created relationship does not exist.

**Additional supporting reproducer 23**
The same current-version family also appears when the later clause re-matches the just-created relationship only to delete it, and the downstream logic depends on that deletion having happened:

```cypher
CREATE (a:Node {id: 1}), (b:Node {id: 2})
WITH a, b
CREATE (a)-[:CONNECTED]->(b)
WITH a, b
MATCH (a)-[r:CONNECTED]->(b)
DELETE r
WITH a, b
MATCH (a)
WHERE NOT (a)-[:CONNECTED]->()
CREATE (a)-[:SELF]->(a)
RETURN count(*) AS total;
```

Observed results:

- Neo4j:

```text
1
```

- FalkorDB `4.18.3`:

```text
0
```

This is not a generic `DELETE` or negative-pattern issue, because if the same `[:CONNECTED]` relationship already exists before the query starts, FalkorDB agrees with Neo4j and returns `1`. The failure is specifically that the relationship created earlier in the same query is not being re-bound by the later `MATCH`, so the downstream `DELETE` and `WHERE NOT ...` logic never sees the intended row.

**Additional supporting reproducer 24**
The same current-version family also appears when a Cartesian outer pipeline creates many pairwise relationships and the later clause tries to re-bind each one immediately:

```cypher
CREATE (:Person {name:'Alice'}), (:Person {name:'Bob'}), (:Person {name:'Charlie'})

MATCH (p:Person)
WITH p, p.name AS name
ORDER BY name
WITH collect(p) AS persons
UNWIND persons AS p1
UNWIND persons AS p2
WITH p1, p2
WHERE p1 <> p2
CREATE (p1)-[:KNOWS]->(p2)
WITH p1, p2
MATCH (p1)-[r:KNOWS]->(p2)
RETURN p1.name, p2.name, count(r) AS cnt
ORDER BY p1.name, p2.name;
```

Observed results:

- Neo4j:

```text
Alice,   Bob,     1
Alice,   Charlie, 1
Bob,     Alice,   1
Bob,     Charlie, 1
Charlie, Alice,   1
Charlie, Bob,     1
```

- FalkorDB `4.18.3`:

```text
0 rows
```

So the family is not limited to one or a few relationships created upstream. Even when the query creates a full set of pairwise edges, the later re-match can still behave as if none of those just-created relationships exist.

**Additional supporting reproducer 25**
The same current-version family also appears when several relationships are created across `UNWIND` rows and the later clause aggregates over the intended re-bound edges:

```cypher
CREATE (:Person {name:'Alice'}), (:Person {name:'Bob'}), (:City {name:'NYC'}), (:City {name:'LA'})

MATCH (p:Person), (c:City)
WITH p, c
UNWIND range(1, 3) AS i
CREATE (p)-[:VISITED {count: i}]->(c)
WITH p, c, i
MATCH (p)-[r:VISITED]->(c)
RETURN p.name, c.name, sum(r.count) AS total
ORDER BY p.name, c.name;
```

Observed results:

- Neo4j:

```text
Alice, LA,  18
Alice, NYC, 18
Bob,   LA,  18
Bob,   NYC, 18
```

- FalkorDB `4.18.3`:

```text
0 rows
```

So the family is not limited to later simple reads or single-edge counts. Relationships created on multiple upstream rows can all disappear from a later re-match even when the downstream clause only wants to aggregate over them.

**Additional supporting reproducer 26**
The same current-version family also appears in a very small self-loop form:

```cypher
MATCH (p:Person)
CREATE (p)-[:KNOWS]->(p)
WITH p
MATCH (p)-[r:KNOWS]->(p)
RETURN p.name, count(r) AS self_loops;
```

Observed results:

- Neo4j:

```text
Alice,   1
Bob,     1
Charlie, 1
```

- FalkorDB `4.18.3`:

```text
0 rows
```

So the family is not limited to relationships between distinct endpoints. Even a just-created self-loop can disappear completely from a later direct re-match in the same query.

**Verification Environment**
Plain FalkorDB Docker image is enough.

Preferred verification target:
- Docker image
- x86_64
- `falkordb/falkordb`

I can verify a fix using:
- plain FalkorDB Docker image on Windows host
- project RESP executor / direct `GRAPH.QUERY`

**Stage3 provenance**
This report predates explicit file-level Stage3 provenance tracking.

Later current-version supporting case:

- Stage3 run: `historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b`
- Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_falkordb_4_18_2_skeleton_20260501`
- Query ID: `ArcadeData__arcadedb__3391__q001`
- Case ID: `case_dc5aa4e3d68d`

Later current-version supporting cases:

- Stage3 run: `historical_bug_queries_parallel_llm_v1_feature_only_random_20260506`
- Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_falkordb_4_18_3_feature_only_random_20260507`
- Supporting seeds:
  - `RedisGraph__RedisGraph__218__q001` / `case_4fe38efe423a`
  - `RedisGraph__RedisGraph__521__q011` / `case_a421a67d6272`
  - `RedisGraph__RedisGraph__633__q011` / `case_4cb9257fd7b9`
  - `RedisGraph__RedisGraph__719__q006` / `case_334c83b8587d`
  - `FalkorDB__FalkorDB__775__q003` / `case_7a463d6f00a5`
  - `FalkorDB__FalkorDB__786__q001` / `case_11974e3dbba8`
  - `FalkorDB__FalkorDB__325__q004` / `case_427e842122d6`
  - `FalkorDB__FalkorDB__339__q001` / `case_9d1f60ecd171`
  - `FalkorDB__FalkorDB__378__q005` / `case_b0c0a0bc84c7`
  - `RedisGraph__RedisGraph__361__q007` / `case_faa30a5cd9b2`
  - `RedisGraph__RedisGraph__2417__q001` / `case_74d6337cb561`
  - `RedisGraph__RedisGraph__2750__q002` / `case_c5537afd145a`
  - `RedisGraph__RedisGraph__2773__q003` / `case_1cd95f4c5180`
  - `RedisGraph__RedisGraph__2853__q008` / `case_063648e04c6a`
  - `ArcadeData__arcadedb__3307__q003` / `case_c93b10395138`
  - `FalkorDB__FalkorDB__1221__q001` / `case_f9e29df46ae9`
  - `FalkorDB__FalkorDB__716__q001` / `case_4c27ec0d01bd`
  - `RedisGraph__RedisGraph__1346__q001` / `case_92a0f84c5446`
  - `neo4j__12844__q002` / `case_dfe229cab28a`
  - `neo4j__13712__q004` / `case_6e113e92f7e5`
