**Describe the bug**
`tail()` may return `null` on Apache AGE when the input list has length `0` or `1`.

In Cypher, `tail(list)` should return the input list without its first element. For an empty list or a singleton list, that result should be an empty list `[]`, not `null`.

Instead, AGE returns `null` for these boundary cases.

**How are you accessing AGE (Command line, driver, etc.)?**
- PostgreSQL `cypher(...)` wrapper through the local Python differential-testing harness
- Reproducible directly in `psql` inside the Docker container

**What data setup do we need to do?**
```pgsql
SELECT create_graph('expr_tail_bug');
```

No graph data is required beyond creating an empty graph.

**What is the necessary configuration info needed?**
- Plain Apache AGE Docker image was enough
- Docker image in local repro: `apache/age`
- AGE extension version: `1.7.0`
- PostgreSQL version: `18.1`
- Graph name used in repro: `expr_tail_bug`
- No extra extensions or special configuration were required

**What is the command that caused the error?**
```pgsql
SELECT * FROM cypher('expr_tail_bug', $$
  RETURN tail([]) AS v
$$) AS (v agtype);
```

Returned result on AGE:
```text
null
```

**Expected behavior**
The query should return an empty list:

```text
[]
```

Both Neo4j and Memgraph return `[]` for the equivalent Cypher query.

**Environment (please complete the following information):**
- Version: Apache AGE `1.7.0`
- PostgreSQL: `18.1`
- Host OS: Windows
- Architecture: x86_64
- Deployment: Docker

**Additional context**
Two nearby variants show the same incorrect boundary behavior:

1. Singleton numeric list:

```pgsql
SELECT * FROM cypher('expr_tail_bug', $$
  RETURN tail([1]) AS v
$$) AS (v agtype);
```

Expected result on Neo4j and Memgraph:
```text
[]
```

Observed result on AGE:
```text
null
```

2. Singleton null-valued list:

```pgsql
SELECT * FROM cypher('expr_tail_bug', $$
  RETURN tail([null]) AS v
$$) AS (v agtype);
```

Expected result on Neo4j and Memgraph:
```text
[]
```

Observed result on AGE:
```text
null
```

A non-boundary control case behaves normally on the same AGE instance:

```pgsql
SELECT * FROM cypher('expr_tail_bug', $$
  RETURN tail([1, 2]) AS v
$$) AS (v agtype);
```

Observed result on AGE:
```text
[2]
```

So this appears to be a boundary-condition bug in `tail()` specifically for lists of length at most one, rather than a general failure of the function.
