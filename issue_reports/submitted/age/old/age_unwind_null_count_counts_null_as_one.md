**Describe the bug**
`UNWIND [null]` may produce a row whose value prints as `null`, but `count(x)` still counts it as `1`.

In Cypher, `count(expr)` should count only non-null values. After `UNWIND [null] AS x`, the single row has `x = null`, so `count(x)` should be `0`.

Instead, Apache AGE returns `1`.

**How are you accessing AGE (Command line, driver, etc.)?**
- PostgreSQL `cypher(...)` wrapper through the local Python differential-testing harness
- Reproducible directly in `psql` inside the Docker container

**What data setup do we need to do?**
No graph data is required beyond creating an empty graph:

```pgsql
SELECT create_graph('fuzz_graph');
```

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
  UNWIND [null] AS x
  RETURN count(x) AS c
$$) AS (c agtype);
```

Returned result on AGE:
```text
c
---
1
```

**Expected behavior**
The result should be:

```text
c
---
0
```

Neo4j returns `0` for the equivalent Cypher query:

```cypher
UNWIND [null] AS x
RETURN count(x) AS c
```

**Environment (please complete the following information):**
- Version: Apache AGE `1.7.0`
- PostgreSQL: `18.1`
- Host OS: Windows
- Architecture: x86_64
- Deployment: Docker

**Additional context**
This appears to be a counting/null-semantics issue specifically around `UNWIND`, not a general `count(null)` problem.

Direct control cases on the same AGE instance:

1. Plain `count(null)` behaves correctly:

```pgsql
SELECT * FROM cypher('fuzz_graph', $$
  RETURN count(null) AS c
$$) AS (c agtype);
```

Observed result:
```text
0
```

2. `WITH null AS x RETURN count(x)` also behaves correctly:

```pgsql
SELECT * FROM cypher('fuzz_graph', $$
  WITH null AS x
  RETURN count(x) AS c
$$) AS (c agtype);
```

Observed result:
```text
0
```

3. `UNWIND [null] AS x RETURN x` shows that the produced row is indeed `null`:

```pgsql
SELECT * FROM cypher('fuzz_graph', $$
  UNWIND [null] AS x
  RETURN x
$$) AS (x agtype);
```

Observed result:
```text
null
```

A larger differential-testing case first exposed this via:

```cypher
MATCH (p:Person)
OPTIONAL MATCH path = (p)-[:KNOWS]->(friend:Person)
WHERE p.age > 25
UNWIND [path] AS pth
RETURN p.name AS person_name, count(pth) AS friend_count
```

For rows where `path` was null, AGE returned `friend_count = 1` instead of `0`. Reducing the case to `UNWIND [null]` reproduced the same incorrect counting behavior.

This family also has a stronger current-version wrong-row variant:

```pgsql
SELECT * FROM cypher('fuzz_graph', $$
  UNWIND null AS x
  RETURN 1 AS y
$$) AS (y agtype);
```

Observed AGE result:

```text
1
```

Observed Neo4j result for the equivalent Cypher query:

```text
(0 rows)
```

So AGE is not just miscounting a produced `null` row. It can also preserve a row that Cypher should eliminate entirely.

An outer-row control shows the same thing:

```pgsql
SELECT * FROM cypher('fuzz_graph', $$
  CREATE (a:Person {name: 'Alice'})
  WITH a
  UNWIND null AS x
  RETURN a.name AS name
$$) AS (name agtype);
```

Observed AGE result:

```text
"Alice"
```

Observed Neo4j result:

```text
(0 rows)
```

So this looks broader than a `count(x)` issue. The underlying family appears to be incorrect `UNWIND null` semantics in AGE.

The same broader family surfaced again during automated differential testing in:

```cypher
MATCH (a)-[r:KNOWS]->(c)
WHERE a.name CONTAINS 'Ali'
SET r.since = '2021'
WITH a, c, r
UNWIND null AS x
RETURN a, c, r.since
```

Neo4j returned `0 rows`, while AGE returned the carried row unchanged.

**Stage3 provenance**
- Additional supporting seed from the skeleton run:
  - `RedisGraph__RedisGraph__110__q001` / `case_99e2a45b8c8e`
