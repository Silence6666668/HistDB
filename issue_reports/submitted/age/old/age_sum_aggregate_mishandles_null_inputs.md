**Describe the bug**
`sum(...)` may mishandle `null` inputs.

In Apache AGE, `sum(null)` returns `null` instead of `0`, and `sum(x)` over a list containing both numbers and `null` may fail with:

```text
arguments must resolve to a number
```

On both Neo4j and Memgraph, `sum` ignores null inputs and returns a numeric result.

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
  RETURN sum(null) AS v
$$) AS (v agtype);
```

Returned result on AGE:
```text
null
```

**Expected behavior**
The result should be:

```text
0
```

Neo4j returns `0`, and Memgraph also returns `0`.

**Environment (please complete the following information):**
- Version: Apache AGE `1.7.0`
- PostgreSQL: `18.1`
- Host OS: Windows
- Architecture: x86_64
- Deployment: Docker

**Additional context**
A second variant shows the same issue more strongly:

```pgsql
SELECT * FROM cypher('fuzz_graph', $$
  UNWIND [1,null,2] AS x
  RETURN sum(x) AS v
$$) AS (v agtype);
```

Apache AGE returns:
```text
ERROR: arguments must resolve to a number
```

Expected result:
```text
3
```

Neo4j returns `3`, and Memgraph also returns `3`.

Other nearby control cases behave as expected on the same AGE instance:

1. Summing a non-null number works:

```pgsql
SELECT * FROM cypher('fuzz_graph', $$
  RETURN sum(1) AS v
$$) AS (v agtype);
```

Observed result:
```text
1
```

2. `avg(null)` still returns `null`, which is expected:

```pgsql
SELECT * FROM cypher('fuzz_graph', $$
  RETURN avg(null) AS v
$$) AS (v agtype);
```

Observed result:
```text
null
```

3. Even the single-null `UNWIND` case differs from Neo4j and Memgraph:

```pgsql
SELECT * FROM cypher('fuzz_graph', $$
  UNWIND [null] AS x
  RETURN sum(x) AS v
$$) AS (v agtype);
```

Apache AGE returns:
```text
null
```

while Neo4j and Memgraph both return:
```text
0
```

4. A current-version supporting repro from the skeleton differential shows the same problem on a missing property carried through a normal node aggregate:

```cypher
MERGE (c:Person {name: 'Alice'})
RETURN c.name, count(c) AS cnt, sum(c.age) AS total_age
```

Observed Neo4j result:

```text
Alice, 1, 0
```

Observed AGE result:

```text
"Alice", 1, null
```

So the issue is not limited to explicit `null` literals.
AGE also treats an all-null property projection as `null` in `sum(...)`, where Neo4j treats it as an empty numeric contribution and returns `0`.

So the issue appears to be specifically in how Apache AGE's `sum` aggregate handles null-valued inputs.

**Stage3 provenance**
- Additional supporting seed from the skeleton run:
  - `FalkorDB__FalkorDB__1340__q002` / `case_e1e617990b83`
