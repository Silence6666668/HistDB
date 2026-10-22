done
**Suggested title**
`MATCH immediately after CREATE may not see newly created labeled nodes`

**ArcadeDB version**
Observed on Docker images:

- `arcadedata/arcadedb:26.4.1-SNAPSHOT`
- `arcadedata/arcadedb:26.4.2`

Older images behave differently and reject the same direct shape:

- `arcadedata/arcadedb:latest`
- `arcadedata/arcadedb:26.3.2`

On those older lines, the parser reports:

```text
WITH is required between CREATE and MATCH
```

The `26.4.x` line is different: the query is accepted, but the later `MATCH` still does not see the newly created labeled nodes.

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
On ArcadeDB `26.4.1-SNAPSHOT` and `26.4.2`, a `MATCH` that appears immediately after `CREATE` may fail to see newly created labeled nodes from the same query.

In the minimized repro below, Neo4j creates one `:Person` node and the later `MATCH (n:Person)` sees it immediately, so the count is `1`.
ArcadeDB accepts the query, but the same `MATCH` returns `0`.

This does not look like a general write failure:

- the `CREATE` clause itself is accepted
- adding a `WITH` barrier between `CREATE` and `MATCH` makes ArcadeDB behave correctly
- matching preexisting labeled nodes in a separate query also behaves correctly

So the failure is specifically about immediate same-query visibility of newly created labeled nodes.

**To Reproduce**

Query:

```cypher
CREATE (:Person {name:'Charlie'})
MATCH (n:Person)
RETURN count(*) AS c;
```

**Expected behavior**
Observed Neo4j result:

```text
1
```

ArcadeDB should return the same count, because the newly created `:Person` node is part of the same query.

**Actual behavior**
Observed ArcadeDB `26.4.1-SNAPSHOT` / `26.4.2` result:

```text
0
```

So the later `MATCH (n:Person)` behaves as if the created labeled node does not exist yet.

**Control cases**

If a trivial `WITH` barrier is inserted between `CREATE` and `MATCH`, ArcadeDB behaves correctly:

```cypher
CREATE (:Person {name:'Charlie'})
WITH 1 AS x
MATCH (n:Person)
RETURN count(*) AS c;
```

Observed result on Neo4j and ArcadeDB `26.4.1-SNAPSHOT` / `26.4.2`:

```text
1
```

If two labeled nodes are created, the same failure still appears:

```cypher
CREATE (:Person {name:'Charlie'}),
       (:Person {name:'Diana'})
MATCH (n:Person)
RETURN count(*) AS c;
```

Observed results:

- Neo4j: `2`
- ArcadeDB `26.4.1-SNAPSHOT` / `26.4.2`: `0`

And again, inserting `WITH` restores the expected result:

```cypher
CREATE (:Person {name:'Charlie'}),
       (:Person {name:'Diana'})
WITH 1 AS x
MATCH (n:Person)
RETURN count(*) AS c;
```

Observed result on Neo4j and ArcadeDB `26.4.1-SNAPSHOT` / `26.4.2`:

```text
2
```

Matching preexisting labeled nodes in a separate query is also fine:

```cypher
CREATE (:Person {name:'Alice'}),
       (:Person {name:'Bob'});

MATCH (n:Person)
RETURN count(*) AS c;
```

Observed result on Neo4j and ArcadeDB `26.4.1-SNAPSHOT` / `26.4.2`:

```text
2
```

The boundary is even sharper if labels are removed from both sides. On `26.4.x`, the following unlabeled form behaves correctly:

```cypher
CREATE ({name:'Charlie'})
MATCH (n)
RETURN count(*) AS c;
```

Observed result on Neo4j and ArcadeDB `26.4.1-SNAPSHOT` / `26.4.2`:

```text
1
```

So the bug is not just "write then read in one query". It is specifically tied to a later labeled `MATCH` failing to see newly created labeled nodes unless a `WITH` barrier is inserted.

**Stronger differential-seed variant**

The family first resurfaced in the current skeleton differential in a slightly noisier form:

Setup:

```cypher
CREATE (:Person {name: 'Alice', age: 30}),
       (:Person {name: 'Bob', age: 25});
```

Query:

```cypher
CREATE ({name: 'Charlie'}),
       ({name: 'Diana'})
MATCH (n)
RETURN DISTINCT n.name AS name
ORDER BY name DESC;
```

Observed results:

- Neo4j:

```text
Diana
Charlie
Bob
Alice
```

- ArcadeDB `26.4.1-SNAPSHOT` / `26.4.2`:

```text
Diana
```

Direct minimization shows the cleaner core issue is the immediate post-`CREATE` visibility of newly created nodes to a later `MATCH`, especially in the labeled form above.

**Why this matters**
This is not a parser-only issue on the current line.
ArcadeDB accepts the query and executes it, but the later `MATCH` sees the wrong graph state.
That can silently drop rows from later filters, aggregations, writes, and joins.

**Verification environment**
Verified directly against:

- Neo4j Docker container: `neo4j:latest`
- ArcadeDB Docker container: `arcadedata/arcadedb:latest`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.3.2`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.4.1-SNAPSHOT`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.4.2`
- Local differential-testing harness
- Direct ArcadeDB HTTP execution using the same request mode as Studio

**Stage3 provenance**
- Stage3 run: `historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b`
- Query ID: `RedisGraph__RedisGraph__526__q004`
- Case ID: `case_e68eae1d8b3f`
- Stage3 artifact: `data/batch_runs/historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b/stage3_main/instantiated_cases.jsonl`
- Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_arcadedb_skeleton_20260430`
- Differential bucket: `all_passed_different_result`
