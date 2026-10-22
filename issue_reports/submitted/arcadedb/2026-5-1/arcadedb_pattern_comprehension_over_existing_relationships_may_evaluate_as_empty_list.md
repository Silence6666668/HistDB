**Suggested title**
`Pattern comprehension over existing relationships may evaluate as an empty list`

**ArcadeDB version**
Observed on Docker images:

- `arcadedata/arcadedb:latest`
- `arcadedata/arcadedb:26.3.2`
- `arcadedata/arcadedb:26.4.1-SNAPSHOT`
- `arcadedata/arcadedb:26.4.2`

**Discovered on**
- `2026-05-01`

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
ArcadeDB may evaluate a fixed-length pattern comprehension as an empty list even when matching relationships clearly exist.

In the minimized repro below, the graph contains three `KNOWS` relationships.
Neo4j returns the corresponding projected names.
ArcadeDB returns an empty list `[]`.

This does not look like a general `MATCH` problem, because the same pattern is visible to ordinary `MATCH` and can still be counted correctly.

**To Reproduce**

Setup:

```cypher
CREATE (a:Person {name: 'Alice', age: 30}),
       (b:Person {name: 'Bob', age: 25}),
       (c:Person {name: 'Charlie', age: 35}),
       (a)-[:KNOWS {since: 2020}]->(b),
       (b)-[:KNOWS {since: 2019}]->(c),
       (a)-[:KNOWS {since: 2021}]->(c);
```

Query:

```cypher
RETURN [(p:Person)-[r:KNOWS]->(q:Person) | q.name] AS xs;
```

**Expected behavior**
Observed Neo4j result:

```text
['Bob', 'Charlie', 'Charlie']
```

**Actual behavior**
Observed ArcadeDB result:

```text
[]
```

So the pattern comprehension is behaving as if no relationships matched.

**Control cases**

The same pattern works correctly in an ordinary `MATCH`:

```cypher
MATCH (p:Person)-[r:KNOWS]->(q:Person)
RETURN count(*) AS c, collect(q.name) AS names;
```

Observed result on both Neo4j and ArcadeDB:

```text
c = 3
names = ['Bob', 'Charlie', 'Charlie']
```

So the graph data and relationship traversal are fine. The failure is specific to the pattern-comprehension form.

The problem also reproduces when only a filtered subset should be returned:

```cypher
RETURN [(p:Person)-[r:KNOWS]->(q:Person) WHERE r.since > 2019 | q.name] AS xs;
```

Observed results:

- Neo4j: `['Bob', 'Charlie']`
- ArcadeDB: `[]`

And the same issue appears through `size(...)`:

```cypher
RETURN size([(p:Person)-[r:KNOWS]->(q:Person) | q.name]) AS s;
```

Observed results:

- Neo4j: `3`
- ArcadeDB: `0`

This makes the boundary much clearer:

- fixed-length `MATCH`: correct
- fixed-length pattern comprehension: empty

**Current-version supporting repro from the feature-only random run**

The same family also reproduces on fresh ArcadeDB `26.4.2` when the fixed-length pattern comprehension is fed into a later `UNWIND` pipeline:

Setup:

```cypher
CREATE (a:Person {name: 'Alice', active: true})
CREATE (b:Person {name: 'Bob', active: false})
CREATE (c:Person {name: 'Charlie', active: true})
CREATE (a)-[:KNOWS]->(b)-[:KNOWS]->(c);
```

Query:

```cypher
UNWIND [p IN [(a:Person)-[:KNOWS]->(b:Person) WHERE b.active = true | b.name] | p] AS name
RETURN name
ORDER BY name;
```

Observed results:

- Neo4j `2026.04.0`:

```text
Charlie
```

- fresh ArcadeDB `26.4.2`:

```text
0 rows
```

The direct projection variant shows the same root cause more compactly:

```cypher
RETURN [(a:Person)-[:KNOWS]->(b:Person) WHERE b.active = true | b.name] AS xs;
```

Observed results:

- Neo4j `2026.04.0`: `['Charlie']`
- fresh ArcadeDB `26.4.2`: `[]`

And a bound-start control still works on both engines:

```cypher
MATCH (a:Person {name:'Bob'})
RETURN [(a)-[:KNOWS]->(b:Person) WHERE b.active = true | b.name] AS xs;
```

Observed result on both Neo4j and fresh ArcadeDB `26.4.2`:

```text
['Charlie']
```

So this current-version variant tightens the boundary further:

- unbound fixed-length pattern comprehension over existing relationships: empty / no rows
- bound fixed-length pattern comprehension over the same relationship: correct

**Stronger variant from the current skeleton differential**

The same family also reproduces when the fixed-length pattern comprehension feeds a later `reduce(...)` instead of a simple direct projection:

```cypher
UNWIND [p = ()-[]->() | reduce(a = 0, b IN [n IN nodes(p) WHERE n.age > 20] | a + b.age)] AS total_age
RETURN total_age;
```

Setup:

```cypher
CREATE (a:Person {name: 'Alice', age: 30})-[:KNOWS]->(b:Person {name: 'Bob', age: 25})-[:KNOWS]->(c:Person {name: 'Charlie', age: 35});
```

Observed results:

- Neo4j:

```text
55
60
```

- ArcadeDB `26.4.1-SNAPSHOT` / `26.4.2`:

```text
0 rows
```

So the problem is not limited to simple `[pattern | value]` projections returning `[]`.
The same fixed-length pattern-comprehension result can also vanish completely when it is consumed by a later reduction pipeline.

**Why this is kept separate**
This is different from the existing variable-length pattern-comprehension candidate.

- the older candidate is about matched variable-length pattern comprehensions duplicating elements
- this report is about fixed-length pattern comprehensions collapsing to an empty list

So this is intentionally kept separate.

**Differential seed**
This family surfaced while minimizing:

- `RedisGraph__RedisGraph__2086__q001`

**Stage3 provenance**
- Stage3 run: `historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b`
- Stage3 file: `data/batch_runs/historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b/stage3_main/instantiated_cases.jsonl`
- Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_arcadedb_skeleton_20260430`
- Differential bucket: `all_passed_different_result`
- Example case id: `case_1947ab4e7bcb`

The same family was independently resurfaced in the current skeleton differential as:

- Stage3 run: `historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b`
- Query ID: `FalkorDB__FalkorDB__407__q001`
- Case ID: `case_cccf4d6eb1d8`
- Stage3 artifact: `data/batch_runs/historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b/stage3_main/instantiated_cases.jsonl`
- Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_arcadedb_skeleton_20260430`
- Differential bucket: `all_passed_different_result`

The same family was also independently resurfaced in the current feature-only random run as:

- Stage3 run: `historical_bug_queries_parallel_llm_v1_feature_only_random_20260511`
- Query ID: `RedisGraph__RedisGraph__2614__q014`
- Case ID: `case_99f3a0927571`
- Stage3 artifact: `data/batch_runs/historical_bug_queries_parallel_llm_v1_feature_only_random_20260511/stage3x10/stage3_main/instantiated_cases.jsonl`
- Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_2026_04_0_arcadedb_26_4_2_feature_only_random_20260512b`
- Differential bucket: `all_passed_different_result`
