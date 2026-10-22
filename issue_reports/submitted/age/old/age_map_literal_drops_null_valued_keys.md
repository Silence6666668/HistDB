**Describe the bug**
Map literals may drop keys whose values are `null`.

In Cypher, a map literal like `{a: null}` should still contain the key `a` with a null value. Instead, Apache AGE returns an empty map `{}`.

This also affects larger queries that build maps from optional matches: when all projected values are null, AGE returns `{}` instead of preserving the keys with null values.

**How are you accessing AGE (Command line, driver, etc.)?**
- PostgreSQL `cypher(...)` wrapper through the local Python differential-testing harness
- Reproducible directly in `psql` inside the Docker container

**What data setup do we need to do?**
No graph data is required for the minimal repro beyond creating an empty graph:

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
  RETURN {a: null} AS m
$$) AS (m agtype);
```

Returned result on AGE:
```text
{}
```

**Expected behavior**
The returned map should preserve the key:

```text
{a: null}
```

Neo4j returns a map containing the key with a null value for the equivalent Cypher query:

```cypher
RETURN {a: null} AS m
```

**Environment (please complete the following information):**
- Version: Apache AGE `1.7.0`
- PostgreSQL: `18.1`
- Host OS: Windows
- Architecture: x86_64
- Deployment: Docker

**Additional context**
A two-key control case shows the same behavior:

```pgsql
SELECT * FROM cypher('fuzz_graph', $$
  RETURN {companyName: null, sinceYear: null} AS m
$$) AS (m agtype);
```

AGE returns:
```text
{}
```

Expected result:
```text
{companyName: null, sinceYear: null}
```

Two additional expressions suggest this is a semantic loss of null-valued map entries, not just a display issue for one literal form.

1. `keys({a: null})`:

```pgsql
SELECT * FROM cypher('fuzz_graph', $$
  RETURN keys({a: null}) AS ks
$$) AS (ks agtype);
```

Expected result on Neo4j and Memgraph:
```text
["a"]
```

Observed result on AGE:
```text
[]
```

2. `coalesce({a: null}, null)`:

```pgsql
SELECT * FROM cypher('fuzz_graph', $$
  RETURN coalesce({a: null}, null) AS m
$$) AS (m agtype);
```

Expected result on Neo4j and Memgraph:
```text
{a: null}
```

Observed result on AGE:
```text
{}
```

This also showed up during automated Neo4j-vs-AGE differential testing in a larger query using `OPTIONAL MATCH` and `collect(...)`:

```cypher
MATCH (employee:Person)
OPTIONAL MATCH (employee)-[r:WORKS_FOR]->(company)
WHERE r.since > 2020
WITH employee, collect({companyName: company.name, sinceYear: r.since}) AS workHistory
WHERE size(workHistory) > 0
RETURN employee.name AS name, COUNT { (employee)-[:WORKS_FOR]->() } AS jobCount, workHistory
ORDER BY jobCount DESC
```

For rows where `company.name` and `r.since` were both null, Neo4j returned:

```text
[{companyName: null, sinceYear: null}]
```

while AGE returned:

```text
[{}]
```

That suggests the issue is not just display formatting of a top-level literal, but a semantic loss of null-valued map entries during Cypher evaluation.
