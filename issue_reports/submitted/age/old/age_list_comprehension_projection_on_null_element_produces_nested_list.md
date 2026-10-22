**Describe the bug**
List-comprehension projection over a `null` element may produce a nested list instead of `null`.

In the repro below, Apache AGE evaluates:

```cypher
[x IN [null] | x + 1]
```

as:

```text
[[null, 1]]
```

Instead of:

```text
[null]
```

This also affects mixed lists such as `[1, null, 2]`, where the null element becomes `[null, 1]` instead of staying `null`.

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
  RETURN [x IN [null] | x + 1] AS v
$$) AS (v agtype);
```

Returned result on AGE:
```text
[[null, 1]]
```

**Expected behavior**
The null element should stay null under the arithmetic projection, so the expected result is:

```text
[null]
```

Neo4j returns `[null]`, and Memgraph also returns `[null]`.

**Environment (please complete the following information):**
- Version: Apache AGE `1.7.0`
- PostgreSQL: `18.1`
- Host OS: Windows
- Architecture: x86_64
- Deployment: Docker

**Additional context**
The same issue appears in a mixed list:

```pgsql
SELECT * FROM cypher('fuzz_graph', $$
  RETURN [x IN [1,null,2] | x + 1] AS v
$$) AS (v agtype);
```

Apache AGE returns:
```text
[2, [null, 1], 3]
```

Expected result:
```text
[2, null, 3]
```

Neo4j and Memgraph both return `[2, null, 3]`.

Two nearby control cases behave correctly on the same AGE instance:

1. The same list comprehension without arithmetic projection:

```pgsql
SELECT * FROM cypher('fuzz_graph', $$
  RETURN [x IN [1,null,2] | x] AS v
$$) AS (v agtype);
```

Observed result:
```text
[1, null, 2]
```

2. The arithmetic projection on a list without null elements:

```pgsql
SELECT * FROM cypher('fuzz_graph', $$
  RETURN [x IN [1,2] | x + 1] AS v
$$) AS (v agtype);
```

Observed result:
```text
[2, 3]
```

So the issue appears specifically when the list-comprehension projection expression operates on a `null` element.
