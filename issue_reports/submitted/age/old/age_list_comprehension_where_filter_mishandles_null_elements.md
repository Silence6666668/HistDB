**Describe the bug**
List-comprehension `WHERE` filters may mishandle `null` elements.

In the repro below, Apache AGE drops `null` elements even when the filter explicitly selects them with `x IS NULL`. It also fails to remove `null` elements when the filter is `x IS NOT NULL`.

Both Neo4j and Memgraph return the expected filtered lists.

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
  RETURN [x IN [null,1] WHERE x IS NULL] AS v
$$) AS (v agtype);
```

Returned result on AGE:
```text
[]
```

**Expected behavior**
The `WHERE x IS NULL` filter should keep the null element:

```text
[null]
```

Neo4j returns `[null]`, and Memgraph returns `[null]`.

**Environment (please complete the following information):**
- Version: Apache AGE `1.7.0`
- PostgreSQL: `18.1`
- Host OS: Windows
- Architecture: x86_64
- Deployment: Docker

**Additional context**
The same issue appears more clearly with a second null element:

```pgsql
SELECT * FROM cypher('fuzz_graph', $$
  RETURN [x IN [null,1,null] WHERE x IS NULL] AS v
$$) AS (v agtype);
```

Apache AGE returns:
```text
[]
```

Expected result:
```text
[null, null]
```

Neo4j and Memgraph both return `[null, null]`.

The opposite filter is also wrong on AGE:

```pgsql
SELECT * FROM cypher('fuzz_graph', $$
  RETURN [x IN [null,1] WHERE x IS NOT NULL] AS v
$$) AS (v agtype);
```

Apache AGE returns:
```text
[null, 1]
```

Expected result:
```text
[1]
```

Neo4j and Memgraph both return `[1]`.

For reference, the plain list comprehension without a filter behaves as expected:

```pgsql
SELECT * FROM cypher('fuzz_graph', $$
  RETURN [x IN [null,1] | x] AS v
$$) AS (v agtype);
```

Apache AGE returns:
```text
[null, 1]
```

So the issue appears to be specifically in the `WHERE` filtering phase of list comprehension evaluation when `null` elements are present.
