**Describe the bug**
`MATCH` may fail to see nodes that were created earlier in the same Cypher query after a `WITH` boundary.

In the minimized repro below, the query creates two `Person` nodes, carries them through `WITH`, and then immediately runs `MATCH (x:Person)`. Under Cypher semantics, that `MATCH` should see the two newly created nodes and produce two rows.

Instead, Apache AGE returns no rows at all.

**How are you accessing AGE (Command line, driver, etc.)?**
- PostgreSQL `cypher(...)` wrapper through the local Python differential-testing harness
- Reproducible directly in `psql` inside the Docker container

**What data setup do we need to do?**
```pgsql
SELECT create_graph('fuzz_graph');
```

No graph data is required beyond creating an empty graph.

**What is the necessary configuration info needed?**
- Plain Apache AGE Docker image was enough
- Docker image in local repro: `apache/age`
- AGE extension version: `1.7.0`
- PostgreSQL version: `18.1`
- Graph name used in repro: `fuzz_graph`
- No extra extensions or special configuration were required

**What is the command that caused the error?**
```pgsql
SELECT * FROM cypher('fuzz_graph', $$
  CREATE (a:Person {id: 1, name: 'Alice'}),
         (m:Person {id: 2, name: 'Bob'})
  WITH a, m
  MATCH (x:Person)
  RETURN a, m, x
$$) AS (a agtype, m agtype, x agtype);
```

Returned result on AGE:
```text
(0 rows)
```

**Expected behavior**
The `MATCH (x:Person)` clause should see the two newly created nodes, so the query should return two rows.

Observed behavior on both Neo4j and Memgraph:
```text
a = Alice, m = Bob, x = Alice
a = Alice, m = Bob, x = Bob
```

**Environment (please complete the following information):**
- Version: Apache AGE `1.7.0`
- PostgreSQL: `18.1`
- Host OS: Windows
- Architecture: x86_64
- Deployment: Docker

**Additional context**
A smaller control case without the post-`WITH` `MATCH` behaves normally on the same AGE instance:

```pgsql
SELECT * FROM cypher('fuzz_graph', $$
  CREATE (a:Person {id: 1, name: 'Alice'}),
         (m:Person {id: 2, name: 'Bob'})
  WITH a, m
  RETURN a, m
$$) AS (a agtype, m agtype);
```

AGE returns the two created nodes correctly there.

A filtered variant also fails unexpectedly:

```pgsql
SELECT * FROM cypher('fuzz_graph', $$
  CREATE (a:Person {id: 1, name: 'Alice'}),
         (m:Person {id: 2, name: 'Bob'})
  WITH a, m
  MATCH (x:Person)
  WHERE x.id = 1
  RETURN a, m
$$) AS (a agtype, m agtype);
```

Neo4j and Memgraph return one row, but AGE still returns no rows.

This same family also appeared during automated differential testing in larger queries such as:

```cypher
UNWIND range(1, 3) AS i
CREATE (n:Person {id: i})
SET n.name = 'Person' + toString(i)
WITH n
MATCH (m:Person)
OPTIONAL MATCH path = (n)-[:KNOWS]->(m)
RETURN n, m, path
```

and:

```cypher
MATCH (p:Person)
WITH p ORDER BY p.age DESC LIMIT 2
CREATE (new:City {name: 'Metropolis'})
WITH collect(p) AS people, new
MATCH (c:City)
WHERE c.name = new.name
RETURN c.name AS city, count(people) AS resident_count, people
```

In both of those larger variants, Neo4j and Memgraph return rows, while AGE returns none.

The same visibility problem also shows up in a current skeleton-run variant where the created node is rematched through an anonymous property pattern instead of a label scan:

```cypher
CREATE (n:Person {name: 'Alice', age: 30})
WITH n.name AS name
MATCH ({name: name})
MATCH (m:Person)
RETURN m.name AS result
```

Observed Neo4j result:

```text
Alice
```

Observed AGE result:

```text
(0 rows)
```

So the family is not limited to `MATCH (x:Label)` after `WITH`.
After creating a node, AGE may also fail to see that node when it is rematched through an anonymous property-constrained pattern in the same query.

Another current-version supporting repro shows that the same family also affects later rematching across `UNWIND` rows, not just a single created node:

```cypher
WITH range(1, 5) AS af
UNWIND af AS f
CREATE (p:Person {id: f})
WITH p
MATCH (q:Person)
WHERE q.id = p.id - 1
CREATE (q)-[:KNOWS]->(p)
WITH p
MATCH (r:Person)
RETURN count(r) AS cnt
```

Observed Neo4j result:

```text
20
```

Observed AGE result:

```text
0
```

So the family is not limited to immediately rematching one newly created node by label.
After a `WITH` boundary, AGE may also fail to see nodes that were created in earlier `UNWIND` rows of the same query, which then cascades into later write and read steps returning no rows.

Another current-version supporting repro shows the same invisibility even when the later read is split across two fresh label scans before a relationship write:

```cypher
CREATE (n0:Person {id: 1, name: 'Alice'})
CREATE (n1:Person {id: 2, name: 'Bob'})
WITH n0, n1
MATCH (a:Person)
WHERE a.id = n0.id
MATCH (b:Person)
WHERE b.id = n1.id
CREATE (a)-[r:KNOWS {since: 2020}]->(b)
RETURN a.name AS a, type(r) AS t, b.name AS b
```

Observed Neo4j result:

```text
Alice, KNOWS, Bob
```

Observed AGE result:

```text
(0 rows)
```

So the family is not limited to node invisibility followed by a later read-only `MATCH`.
After a `WITH` boundary, AGE may also fail to see newly created nodes when they are rebound through fresh label scans and then used immediately in a downstream relationship write.

Another current-version supporting repro from the feature-only random run shows the same family in a smaller property-rematch shape:

```cypher
UNWIND [1, 2, 3] AS x
CREATE (n:Node {id: x})
WITH n
MATCH (m:Node)
WHERE m.id = n.id
RETURN m.id AS id, 'constant' AS const
```

Observed Neo4j result:

```text
1, constant
2, constant
3, constant
```

Observed AGE result:

```text
(0 rows)
```

So the family is not limited to rematching newly created `Person` nodes by label.
After a `WITH` boundary, AGE may also fail to see just-created nodes when they are rebound through a simple property equality predicate on the same label.

Another current-version supporting repro shows that the same invisibility can suppress later writes even when the later `MATCH` does not use the carried node variable in the returned output:

```cypher
UNWIND [1, 2, 3] AS x
CREATE (n:Node {id: x})
WITH x, n
MATCH (a:Node {id: 1}), (b:Node {id: 2})
CREATE (a)-[:REL]->(b)
RETURN 1
```

Observed Neo4j result:

```text
1
1
1
```

Observed AGE result:

```text
(0 rows)
```

So the family is not limited to a later read that returns the newly created nodes directly.
After a `WITH` boundary, AGE may also fail to see those nodes early enough that a downstream `MATCH ... CREATE ... RETURN` pipeline contributes no rows at all.

Another current-version supporting repro from the feature-only random run shows that the same family can also suppress a later aggregation over pre-existing setup nodes once the newly created node rows cross a `WITH` boundary:

```cypher
UNWIND range(1, 5) AS i
CREATE (n:Node {id: i})
WITH n
WHERE n.id IN [1, 3, 5]
MATCH (p:Person)
WHERE p.name IN ['Alice', 'Bob']
RETURN p.name, count(n) AS nodeCount
ORDER BY nodeCount DESC
```

Observed Memgraph result:

```text
Bob, 3
Alice, 3
```

Observed AGE result:

```text
(0 rows)
```

So the family is not limited to rematching the just-created nodes themselves.
After a `WITH` boundary, AGE may also fail badly enough that a later `MATCH` against already existing setup nodes contributes no rows at all once the pipeline depends on earlier created-node rows.

Another current-version supporting repro shows the same row-loss shape even when the later step is a write against pre-existing setup nodes rather than a read-only aggregation:

```cypher
UNWIND [null] AS x
WITH x
WHERE x IS NULL
CREATE (n:Node {name: 'test'})
WITH n
MATCH (p:Person)
CREATE (n)-[:RELATES_TO]->(p)
RETURN n
```

Observed Memgraph result:

```text
{name: "test"}
{name: "test"}
```

Observed AGE result:

```text
(0 rows)
```

So the family is not limited to later reads after node creation.
Once a newly created node is carried across `WITH`, AGE may also lose the row stream before a later `MATCH ... CREATE ... RETURN` against pre-existing setup nodes.

**Stage3 provenance**
- Additional supporting seeds from the skeleton run:
  - `FalkorDB__FalkorDB__747__q001` / `case_14c48a43ae9e`
  - `FalkorDB__FalkorDB__378__q002` / `case_55f2dd871a43`
  - `FalkorDB__FalkorDB__735__q009` / `case_7e6a1413fbdb`
- Additional supporting seeds from the feature-only random run:
  - `neo4j__13399__q002` / `case_62fc804097ee`
  - `ArcadeData__arcadedb__3612__q004` / `case_31b84a819350`
  - `FalkorDB__FalkorDB__1300__q001` / `case_da146467abf9`
  - `FalkorDB__FalkorDB__1485__q001` / `case_2b873b04dd5b`
