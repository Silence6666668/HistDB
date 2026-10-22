done
**Suggested title**
`Uncorrelated pattern predicate may evaluate as false even when relationships exist`

**ArcadeDB version**
Observed on Docker images:

- `arcadedata/arcadedb:latest`
- `arcadedata/arcadedb:26.3.2`
- `arcadedata/arcadedb:26.4.1-SNAPSHOT`
- `arcadedata/arcadedb:26.4.2`

**Discovered on**
- `2026-04-24`

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
ArcadeDB may evaluate a bare uncorrelated pattern predicate such as:

```cypher
()-[]->()
```

as if it were `false`, even when the graph clearly contains relationships.

In Cypher, this kind of bare pattern in `WHERE` is an existence test. If any matching relationship exists in the graph, the predicate should evaluate to `true` for every outer row.

**To Reproduce**

Setup:

```cypher
CREATE (:Person {name:'Alice'}),
       (:Person {name:'Bob'});

MATCH (a:Person {name:'Alice'}), (b:Person {name:'Bob'})
CREATE (a)-[:KNOWS]->(b);
```

Query:

```cypher
MATCH (p:Person)
WHERE ()-[]->()
RETURN p.name AS name
ORDER BY name;
```

**Expected behavior**
Since the graph contains a relationship, the predicate should be true for every row.

Observed Neo4j result:

```text
Alice
Bob
```

**Actual behavior**
Observed ArcadeDB result:

```text
0 rows
```

So the uncorrelated pattern predicate behaves as if no relationship exists.

**Control cases**

If the same existence test is expressed as an `EXISTS` subquery, ArcadeDB behaves correctly:

```cypher
MATCH (p:Person)
WHERE EXISTS { MATCH ()-[]->() }
RETURN p.name AS name
ORDER BY name;
```

Observed result on both Neo4j and ArcadeDB:

```text
Alice
Bob
```

The polarity is also inverted incorrectly under `NOT`:

```cypher
MATCH (p:Person)
WHERE NOT ()-[]->()
RETURN p.name AS name
ORDER BY name;
```

Expected:

```text
0 rows
```

Observed ArcadeDB result:

```text
Alice
Bob
```

If the graph has no relationships, ArcadeDB agrees with Neo4j:

```cypher
CREATE (:Person {name:'Alice'}),
       (:Person {name:'Bob'});

MATCH (p:Person)
WHERE ()-[]->()
RETURN p.name AS name
ORDER BY name;
```

Observed result on both Neo4j and ArcadeDB:

```text
0 rows
```

So the issue is specifically in how ArcadeDB evaluates the bare uncorrelated pattern predicate, not in setup or row production more generally.

**Stronger reproducer from the current skeleton differential**
The same family also appears in a slightly larger filtered query:

Setup:

```cypher
CREATE (:Person {name:'Alice', age:30}),
       (:Person {name:'Bob', age:25}),
       (:Person {name:'Charlie', age:35}),
       (:Person {name:'Dave', age:18});

MATCH (a:Person {name:'Alice'}), (b:Person {name:'Bob'})
CREATE (a)-[:KNOWS {since:2020}]->(b);
```

Query:

```cypher
MATCH (n:Person)
WHERE n.age > 20
WITH n
WHERE ()<-[:KNOWS {since: 2020}]-()
RETURN n.name AS name
ORDER BY n.name;
```

Observed result:

- Neo4j: `Alice`, `Bob`, `Charlie`
- ArcadeDB: `0 rows`

So the same bug also affects filtered pipelines after `WITH`, not just the simplest top-level shape.

The same family also appears under `NOT` in the current skeleton differential:

Setup:

```cypher
CREATE (a:Root {name: 'root1'})-[:OWNS]->(b:Child {name: 'child1'}),
       (c:Root {name: 'root2'})-[:OWNS]->(d:Child {name: 'child2'}),
       (e:Root {name: 'root3'})-[:OWNS]->(f:Child {name: 'child3'});
```

Query:

```cypher
MATCH (roots:Root)
WHERE NOT ()<-[:OWNS]-(:Root)
WITH roots
MATCH (roots)-[s:OWNS]->(b:Child)
RETURN count(*) AS cnt;
```

Observed results:

- Neo4j: `0`
- ArcadeDB `26.4.1-SNAPSHOT`: `3`
- ArcadeDB `26.4.2`: `3`

So the uncorrelated pattern predicate is not merely flipped in the simplest `WHERE ()-[]->()` shape.
The same wrong truth value persists through a `WITH` pipeline, under `NOT`, and with a typed relationship pattern.

The same current-version family also appears when the false predicate gates a later write:

Setup:

```cypher
CREATE (a:Person {name:'Alice'})-[:KNOWS]->(b:Person {name:'Bob'});
```

Query:

```cypher
MATCH (a:Person)-[:KNOWS]->(b:Person)
WHERE ()-->()
WITH a, b
CREATE (c:Person {name:'Charlie'})
RETURN a.name AS a, b.name AS b, c.name AS c;
```

Observed results:

- Neo4j: `Alice, Bob, Charlie`
- ArcadeDB `26.4.2`: `0 rows`

So this family does not only affect read-only filtering.
The same wrong false value can also suppress a later write path that should have executed.

The same family was independently resurfaced again in the current feature-only random run in both plain `WHERE` filtering and later-pipeline filtering:

Setup:

```cypher
CREATE (a:Person {name: 'Alice'}),
       (b:Person {name: 'Bob'}),
       (c:Person {name: 'Charlie'}),
       (a)-[:KNOWS]->(b),
       (b)-[:KNOWS]->(c),
       (c)-[:KNOWS]->(a);
```

Query:

```cypher
MATCH (n:Person)-[r]->(m:Person)
WHERE ()--()
WITH n, m, r
SKIP 0
CALL { RETURN 1 AS dummy }
RETURN n.name, m.name, type(r)
ORDER BY n.name;
```

Observed results:

- Neo4j `2026.04.0`:
  - `3 rows`
- fresh ArcadeDB `26.4.2`:
  - `0 rows`

And a second variant shows the same false predicate surviving through aggregation and `OPTIONAL MATCH`:

Setup:

```cypher
CREATE (a:Node {name: 'A'}),
       (b:Node {name: 'B'}),
       (c:Node {name: 'C'})
CREATE (a)-[:REL]->(b)-[:REL]->(c);
```

Query:

```cypher
MATCH (n:Node)
OPTIONAL MATCH (n)-[*]->(m)
WITH n, collect(m.name) AS names
WHERE ()--() OR n.name IS NULL
RETURN n.name, names, 1 AS literal;
```

Observed results:

- Neo4j `2026.04.0`:
  - `3 rows`
- fresh ArcadeDB `26.4.2`:
  - `0 rows`

So this current-version resurfacing is consistent with the same root failure:

- a bare uncorrelated pattern predicate such as `()--()` is evaluated as false
- later `WITH`, `CALL`, `OPTIONAL MATCH`, aggregation, or `OR` composition do not fix that wrong truth value

**Differential seeds**
This family was first noticed while minimizing:

- `RedisGraph__RedisGraph__465__q001`

The current skeleton run also produced another clear instance:

- `FalkorDB__FalkorDB__1318__q001`

**Why this matters**
This is not a serialization issue or unsupported-feature issue. The query is accepted and executed, but a pattern-existence predicate receives the wrong truth value. That can silently drop rows in filters and distort later aggregation, writes, and joins.

**Stage3 provenance**
- Stage3 run: `historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b`
- Stage4/45 run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_arcadedb_skeleton_20260430`
- Additional differential seed query id: `FalkorDB__FalkorDB__1318__q001`
- Additional differential seed case id: `case_727540c351ff`
- Additional skeleton-run seed query id: `RedisGraph__RedisGraph__1083__q001`
- Additional skeleton-run seed case id: `case_8fd74181cfb0`

The same family was independently resurfaced again in the feature-only random differential:

- Stage3 run: `historical_bug_queries_parallel_llm_v1_feature_only_random_20260506`
- Query ID: `FalkorDB__FalkorDB__1463__q002`
- Case ID: `case_a895624486f4`
- Stage3 artifact: `data/batch_runs/historical_bug_queries_parallel_llm_v1_feature_only_random_20260506/stage3x10/stage3_main/instantiated_cases.jsonl`
- Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_arcadedb_26_4_2_feature_only_random_20260507`
- Differential bucket: `all_passed_different_result`

The same family was independently resurfaced again in the current feature-only random differential as:

- Stage3 run: `historical_bug_queries_parallel_llm_v1_feature_only_random_20260511`
- Query ID: `RedisGraph__RedisGraph__2991__q004`
- Case ID: `case_16b42ff6ac04`
- Query ID: `neo4j__12883__q001`
- Case ID: `case_ae0c68a01b48`
- Stage3 artifact: `data/batch_runs/historical_bug_queries_parallel_llm_v1_feature_only_random_20260511/stage3x10/stage3_main/instantiated_cases.jsonl`
- Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_2026_04_0_arcadedb_26_4_2_feature_only_random_20260512b`
- Differential bucket: `all_passed_different_result`
