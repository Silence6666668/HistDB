**Suggested title**
`Map projection with a function-call computed field may return only the function result instead of a map`

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
ArcadeDB may evaluate a node map projection incorrectly when one computed field is defined by a function call.

Instead of returning the projected map, ArcadeDB returns only the function result.

In the minimized repro below, Neo4j correctly returns:

```text
{name: 'Alice', upperName: 'ALICE'}
```

ArcadeDB returns only:

```text
ALICE
```

So the projection target `p` and the other projected fields disappear entirely.

This is not just a column-name issue.
Even with an explicit `AS proj` alias, the value itself is wrong.

**To Reproduce**

Setup:

```cypher
CREATE (:Person {name:'Alice'});
```

Query:

```cypher
MATCH (p:Person)
RETURN p {.name, upperName: toUpper(p.name)} AS proj;
```

**Expected behavior**
Observed Neo4j result:

```text
{name: 'Alice', upperName: 'ALICE'}
```

ArcadeDB should return the same projected map.

**Actual behavior**
Observed ArcadeDB result on all tested versions:

```text
ALICE
```

So the function result replaces the whole map projection.

**Additional function-call variant**
The same bad behavior also appears with `size(...)`:

```cypher
MATCH (p:Person)
WITH p, [] AS friends
RETURN p {.name, friendCount: size(friends)} AS proj;
```

Observed results:

- Neo4j:

```text
{name: 'Alice', friendCount: 0}
```

- ArcadeDB `latest / 26.3.2 / 26.4.1-SNAPSHOT / 26.4.2`:

```text
0
```

So this is not specific to one string function.
More generally, a computed field based on a function call can replace the entire projection result.

**Control cases**

A plain map projection without a computed field works:

```cypher
MATCH (p:Person)
RETURN p {.name} AS proj;
```

Observed result on Neo4j and all tested ArcadeDB versions:

```text
{name: 'Alice'}
```

A computed field with a constant value also works:

```cypher
MATCH (p:Person)
RETURN p {.name, friendCount: 0} AS proj;
```

Observed result on Neo4j and all tested ArcadeDB versions:

```text
{name: 'Alice', friendCount: 0}
```

A computed field with a non-function expression also works:

```cypher
MATCH (p:Person)
RETURN p {.name, nameCopy: p.name + ''} AS proj;
```

Observed result on Neo4j and all tested ArcadeDB versions:

```text
{name: 'Alice', nameCopy: 'Alice'}
```

So the bad behavior is not about computed fields in general.
The failure starts when the computed field expression is a function call such as:

- `toUpper(p.name)`
- `size(friends)`

**Original differential seed**

This family first resurfaced in the current feature-only random differential in a noisier path-and-list variant:

```cypher
MATCH (p:Person)
OPTIONAL MATCH (p)-[:KNOWS|FRIEND*1..2]->(q:Person)
WITH p, collect(DISTINCT q) AS friends, [x IN [1, 2, 3] WHERE false | x] AS emptyList
RETURN p {.name, friendCount: size(friends), emptyList}
ORDER BY p.name;
```

Observed results:

- Neo4j:

```text
{name: 'Alice', friendCount: 0, emptyList: []}
{name: 'Bob', friendCount: 0, emptyList: []}
{name: 'Charlie', friendCount: 0, emptyList: []}
```

- ArcadeDB `26.4.2`:

```text
0
0
0
```

Direct minimization shows that the optional match and the extra empty-list field are not required.
The smaller root cause is the function-call computed field inside the map projection.

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
  - `neo4j__13093__q004` / `case_a860cbe23731`
