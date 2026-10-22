**Describe the bug**
List comprehension over a collected node list may fail when accessing node properties.

In the repro below, `collect(p)` correctly produces a list of nodes, but the subsequent list comprehension `[person IN people | person.name]` fails with:

```text
could not find properties for person
```

Under Cypher semantics, the expression should return the list of names from the collected nodes.

**How are you accessing AGE (Command line, driver, etc.)?**
- PostgreSQL `cypher(...)` wrapper through the local Python differential-testing harness
- Reproducible directly in `psql` inside the Docker container

**What data setup do we need to do?**
```pgsql
SELECT * FROM cypher('fuzz_graph', $$
  CREATE (:Person {name:'Alice'}),
         (:Person {name:'Bob'})
$$) AS (v agtype);
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
  MATCH (p:Person)
  WITH collect(p) AS people
  RETURN [person IN people | person.name] AS names
$$) AS (names agtype);
```

Returned result on AGE:
```text
ERROR: could not find properties for person
```

**Expected behavior**
The query should return:

```text
["Alice", "Bob"]
```

Neo4j returns exactly that result for the equivalent Cypher query.

**Environment (please complete the following information):**
- Version: Apache AGE `1.7.0`
- PostgreSQL: `18.1`
- Host OS: Windows
- Architecture: x86_64
- Deployment: Docker

**Additional context**
Two nearby control cases behave correctly on the same AGE instance:

1. Collecting nodes without the property-accessing list comprehension works:

```pgsql
SELECT * FROM cypher('fuzz_graph', $$
  MATCH (p:Person)
  WITH collect(p) AS people
  RETURN size(people) AS n
$$) AS (n agtype);
```

Observed result:
```text
2
```

2. List comprehension over collected scalar values also works:

```pgsql
SELECT * FROM cypher('fuzz_graph', $$
  MATCH (p:Person)
  WITH collect(p.name) AS names
  RETURN [name IN names | name] AS echoed
$$) AS (echoed agtype);
```

Observed result:
```text
["Alice", "Bob"]
```

So the failure appears to be specific to property access on node values inside a list comprehension after `collect(p)`.

This was first noticed during automated Neo4j-vs-AGE differential testing in a larger query:

```cypher
MATCH (p:Person)
WHERE EXISTS { MATCH (p)-[:LIVING_IN]->(c:Country) RETURN c.name }
WITH COLLECT(p) AS people
OPTIONAL MATCH path = (p2:Person)-[:LIVING_IN]->(c2:Country)-[:HAS_CAPITAL]->(ci:City)
WITH people, COLLECT(path) AS paths
RETURN CASE WHEN size(paths) > 0 THEN 'Paths found' ELSE 'No paths' END AS status,
       [person IN people | person.name] AS person_names
ORDER BY status DESC
LIMIT 5
```

After minimization, the same failure still reproduces with the much smaller query above, so this appears to be an engine-level issue rather than a result-formatting artifact.
