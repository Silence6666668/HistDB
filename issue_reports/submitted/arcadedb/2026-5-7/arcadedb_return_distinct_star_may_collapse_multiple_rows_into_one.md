done
**Suggested title**
`RETURN DISTINCT * may collapse multiple rows into one`

**ArcadeDB version**
Observed on Docker images:

- `arcadedata/arcadedb:latest`
- `arcadedata/arcadedb:26.3.2`
- `arcadedata/arcadedb:26.4.1-SNAPSHOT`
- `arcadedata/arcadedb:26.4.2`

The same result was also rechecked on a fresh `26.4.2` container.

**Discovered on**
- `2026-05-07`

**Environment**
- Host OS: Windows
- Architecture: x86_64
- Deployment: Docker
- ArcadeDB endpoint: HTTP `/api/v1/command/arcade`
- Request mode matches ArcadeDB Studio:
  - `language: opencypher`
  - `serializer: studio`
- Differential comparison target: Neo4j Docker `neo4j:latest`

**Describe the bug**
ArcadeDB may collapse multiple distinct rows into a single row under `RETURN DISTINCT *`.

In the minimized repro below, there are clearly two matched `Person` nodes.
Neo4j returns both rows.
ArcadeDB returns only one row.

This is not just a serialization difference in how nodes are displayed.
The row count itself is wrong.

**To Reproduce**

Setup:

```cypher
CREATE (:Person {name:'Alice'}),
       (:Person {name:'Bob'});
```

Query:

```cypher
MATCH (p:Person)
RETURN DISTINCT *;
```

**Expected behavior**
Observed Neo4j result:

```text
Alice
Bob
```

ArcadeDB should also return two rows.

**Actual behavior**
Observed ArcadeDB result on all tested versions:

```text
Alice
```

So one matched row disappears entirely under `RETURN DISTINCT *`.

**Control cases**

Without `DISTINCT`, ArcadeDB behaves correctly:

```cypher
MATCH (p:Person)
RETURN *;
```

Observed result on Neo4j and all tested ArcadeDB versions:

```text
Alice
Bob
```

If the variable is returned explicitly rather than through `*`, ArcadeDB also behaves correctly:

```cypher
MATCH (p:Person)
RETURN DISTINCT p;
```

Observed result on Neo4j and all tested ArcadeDB versions:

```text
Alice
Bob
```

The same behavior also reproduces after a trivial `WITH`:

```cypher
MATCH (p:Person)
WITH p
RETURN DISTINCT *;
```

Observed results:

- Neo4j: `2 rows`
- ArcadeDB `latest / 26.3.2 / 26.4.1-SNAPSHOT / 26.4.2`: `1 row`

So the problem is not general `DISTINCT`, and it is not general star projection.
The bad behavior starts specifically at `RETURN DISTINCT *`.

**Original differential variant**

This family first resurfaced in the current feature-only random differential in a noisier collected-list shape:

```cypher
UNWIND [1, 2] AS x
UNWIND ['Alice', 'Bob'] AS y
MATCH (p:Person {name: y})
WITH collect(p) AS persons
MERGE (city:City {name: 'NYC'})
  ON CREATE SET city.population = 1000000
WITH city, persons
UNWIND persons AS person
MERGE (city)<-[:LOCATED_IN]-(person)
RETURN DISTINCT *, person.name, city.name AS city_name;
```

Observed results:

- Neo4j: `2 rows`
- ArcadeDB `26.4.2`: `1 row`

Direct minimization shows that the write clauses, extra aliases, and collected list are not required.
The smaller root cause is already visible in the plain:

```cypher
MATCH (p:Person)
RETURN DISTINCT *;
```

**Additional current-version supporting repro**

The same family also resurfaced again in a fresh `26.4.2` rerun in a slightly richer write shape:

Setup:

```cypher
CREATE (a:Person {name: 'Alice', age: 30}),
       (b:Person {name: 'Bob', age: 25}),
       (c:Person {name: 'Charlie', age: 35});
```

Query:

```cypher
MATCH (p:Person)
WITH p, [x IN range(1, 3) | x * p.age] AS ages
CREATE (p)-[:KNOWS]->(:Person {name: p.name + '_friend', age: ages[0]})
RETURN DISTINCT *;
```

Observed results:

- Neo4j `2026.04.0`: `3 rows`
- Memgraph `3.9.0`: `3 rows`
- ArcadeDB fresh `26.4.2`: `1 row`

So this is not limited to the tiny read-only repro above. The same row-collapse still happens after a write, as long as the final projection is `RETURN DISTINCT *`.

And the failure still stays sharply tied to the wildcard-distinct form. If the same query returns an explicit projection instead, all three engines agree:

```cypher
MATCH (p:Person)
WITH p, [x IN range(1, 3) | x * p.age] AS ages
CREATE (p)-[:KNOWS]->(:Person {name: p.name + '_friend', age: ages[0]})
RETURN DISTINCT p.name AS name, ages
ORDER BY name;
```

Observed result on Neo4j `2026.04.0`, Memgraph `3.9.0`, and ArcadeDB fresh `26.4.2`:

```text
Alice, [30, 60, 90]
Bob, [25, 50, 75]
Charlie, [35, 70, 105]
```

So the write itself is not enough to break the query. The collapse still starts specifically at `RETURN DISTINCT *`.

**Why this is kept separate**
This is related to:

- [arcadedb_return_distinct_star_with_appended_alias_may_not_allow_order_by_alias.md](D:/sci/works/get%20bugs/graph-bug-fuzz-mvp/issue_reports/submitted/arcadedb/2026-5-1/arcadedb_return_distinct_star_with_appended_alias_may_not_allow_order_by_alias.md)

but the boundary is different.

That report is about `ORDER BY` resolution when `RETURN DISTINCT *` also appends an alias.
This report reproduces even without any appended alias and even without `ORDER BY`.
The failure here is that rows are collapsed away.

So this is kept separate.

**Verification environment**
Verified directly against:

- Neo4j Docker container: `neo4j:latest`
- ArcadeDB Docker container: `arcadedata/arcadedb:latest`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.3.2`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.4.1-SNAPSHOT`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.4.2`
- Fresh ArcadeDB Docker container: `arcadedata/arcadedb:26.4.2`
- Local differential-testing harness
- Direct ArcadeDB HTTP execution using the same request mode as Studio

**Stage3 provenance**
- Stage3 run: `historical_bug_queries_parallel_llm_v1_feature_only_random_20260506`
- Stage3 artifact: `data/batch_runs/historical_bug_queries_parallel_llm_v1_feature_only_random_20260506/stage3x10/stage3_main/instantiated_cases.jsonl`
- Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_arcadedb_26_4_2_feature_only_random_20260507`
- Differential bucket: `all_passed_different_result`
- Example source query from this run:
  - `RedisGraph__RedisGraph__2970__q001` / `case_a5286971a003`

Additional current supporting provenance:

- Stage3 run: `historical_bug_queries_parallel_llm_v1_feature_only_random_20260511`
- Stage3 artifact: `data/batch_runs/historical_bug_queries_parallel_llm_v1_feature_only_random_20260511/stage3x10/stage3_main/instantiated_cases.jsonl`
- Differential run: `historical_bug_queries_parallel_llm_v1_stage45_memgraph_arcadedb_26_4_2_fresh_feature_only_random_20260513`
- Differential bucket: `all_passed_different_result`
- Example source query from this run:
  - `ArcadeData__arcadedb__3257__q001` / `case_f2387f49e14a`
