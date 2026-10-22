done
**Suggested title**
`EXISTS { ... } subqueries that return an outer-variable expression may evaluate as false`

**ArcadeDB version**
Observed on Docker images:

- `arcadedata/arcadedb:26.4.1-SNAPSHOT`
- `arcadedata/arcadedb:26.4.2`

Older explicit lines reject the same forms with parse errors instead of producing a boolean result:

- `arcadedata/arcadedb:latest`
- `arcadedata/arcadedb:26.3.2`

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
On ArcadeDB `26.4.1-SNAPSHOT` and `26.4.2`, an `EXISTS { ... }` subquery may evaluate as `false` when the subquery body returns an expression derived from an outer variable.

In the minimized repro below, the outer `MATCH (n)` clearly binds rows.
Neo4j returns `true`, because the `EXISTS` body returns one row per outer row:

```cypher
RETURN n AS x
```

ArcadeDB instead returns `false`.

This does not look like a general `EXISTS` failure.
Constant-return and inner-local-variable variants work correctly on ArcadeDB.
The failure starts when the subquery body returns an expression based on the outer variable.

**To Reproduce**

Setup:

```cypher
CREATE (:Node {id: 1, name:'test'}),
       (:Node {id: 2, name:'other'});
```

Query:

```cypher
MATCH (n)
RETURN exists { RETURN n AS x } AS result
ORDER BY result
LIMIT 1;
```

**Expected behavior**
Observed Neo4j result:

```text
result = true
```

ArcadeDB should return the same boolean value.

**Actual behavior**
Observed ArcadeDB results:

```text
26.4.1-SNAPSHOT: result = false
26.4.2:          result = false
```

So the `EXISTS` subquery behaves as if the returned outer-variable row does not exist.

**Control cases**

If the subquery returns a constant row, ArcadeDB behaves correctly:

```cypher
MATCH (n)
RETURN exists { RETURN 1 AS x } AS result
ORDER BY result
LIMIT 1;
```

Observed result on Neo4j and ArcadeDB `26.4.1-SNAPSHOT` / `26.4.2`:

```text
result = true
```

If the subquery returns an inner-local variable from a normal inner match, ArcadeDB also behaves correctly:

```cypher
MATCH (n)
RETURN exists {
  MATCH (m:Node)
  RETURN m AS x
} AS result
ORDER BY result
LIMIT 1;
```

Observed result on Neo4j and ArcadeDB `26.4.1-SNAPSHOT` / `26.4.2`:

```text
result = true
```

If the subquery returns an outer-variable property expression, the same failure appears:

```cypher
MATCH (n)
RETURN exists { RETURN n.id AS x } AS result
ORDER BY result
LIMIT 1;
```

Observed results:

- Neo4j: `true`
- ArcadeDB `26.4.1-SNAPSHOT`: `false`
- ArcadeDB `26.4.2`: `false`

If the subquery returns a pure aggregate that does not depend on an outer-variable expression, ArcadeDB behaves correctly again:

```cypher
MATCH (n)
RETURN exists { RETURN count(*) AS c } AS result
ORDER BY result
LIMIT 1;
```

Observed result on Neo4j and ArcadeDB `26.4.1-SNAPSHOT` / `26.4.2`:

```text
result = true
```

This makes the boundary much clearer:

- `EXISTS { RETURN 1 }`: correct
- `EXISTS { MATCH (m) RETURN m }`: correct
- `EXISTS { RETURN count(*) }`: correct
- `EXISTS { RETURN n }`: incorrect
- `EXISTS { RETURN n.id }`: incorrect

So the failure is not about `EXISTS` in general.
It is about returning an expression derived from the outer variable.

**Stronger differential seeds**
The same family also surfaced in larger queries from this skeleton run, including:

```cypher
MATCH (n)
RETURN exists { WITH n RETURN n } AS result
```

and:

```cypher
MATCH (n)
CALL {
  WITH n
  RETURN exists { RETURN min(n) } AS result
}
RETURN result
```

In both cases, Neo4j returns `true` while ArcadeDB returns `false`.

Direct reduction shows the root issue is not the surrounding `CALL`.
The core problem is that an `EXISTS` body returning an outer-variable-derived expression can be evaluated as if it produced no row.

**Relationship-pattern variant from the current feature-only random run**
The same family also appears when the outer row already binds a relationship variable and the `EXISTS` predicate is used in `WHERE` rather than `RETURN`.

Setup:

```cypher
CREATE (a:Person {name: 'Alice'})-[:KNOWS {since: 2020}]->(b:Person {name: 'Bob'});
```

If the `EXISTS` body returns an outer node variable, ArcadeDB drops the row:

```cypher
MATCH (x:Person)-[r:KNOWS]->(y:Person)
WHERE EXISTS { RETURN x AS dummy }
RETURN x.name AS x, y.name AS y;
```

Observed results:

- Neo4j: `Alice, Bob`
- ArcadeDB `26.4.2`: `0 rows`

The same happens when the `EXISTS` body returns the bound relationship variable:

```cypher
MATCH (x:Person)-[r:KNOWS]->(y:Person)
WHERE EXISTS { RETURN r AS dummy }
RETURN x.name AS x, y.name AS y;
```

Observed results:

- Neo4j: `Alice, Bob`
- ArcadeDB `26.4.2`: `0 rows`

But the constant-return control still behaves correctly:

```cypher
MATCH (x:Person)-[r:KNOWS]->(y:Person)
WHERE EXISTS { RETURN 1 AS dummy }
RETURN x.name AS x, y.name AS y;
```

Observed result on Neo4j and ArcadeDB `26.4.2`:

```text
Alice, Bob
```

The original generated query from this run also fits the same boundary:

```cypher
MATCH p = (x:Person)-[r:KNOWS]->(y:Person)
WHERE EXISTS {
  MATCH (x)-[r]->(y)
  RETURN 1 AS dummy
}
RETURN id(r) AS rel_id, x.`name` AS name_x, y.`name` AS name_y, length(p) AS path_len, 42 AS constant;
```

Observed results:

- Neo4j: `1 row`
- ArcadeDB `26.4.2`: `0 rows`

Fresh-container reduction shows that this is not a separate `MATCH (x)-[r]->(y)` bug.
The simpler `WHERE EXISTS { RETURN x }` and `WHERE EXISTS { RETURN r }` forms already reproduce the same false-negative behavior on a fresh `26.4.2` container.

**Why this is kept separate**
This is related to the existing ArcadeDB `EXISTS` reports, but the boundary is different.

- the `inner WITH` report is about `EXISTS { WITH ... }` specifically
- the `CALL` / `UNWIND` / `OPTIONAL MATCH` / `IN predicate` reports are about particular body clauses
- this report is about the value returned by the `EXISTS` body: returning an outer-variable expression can flip the boolean to `false`

So this is intentionally kept separate.

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
- Stage3 artifact: `data/batch_runs/historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b/stage3_main/instantiated_cases.jsonl`
- Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_arcadedb_skeleton_20260430`
- Differential bucket: `all_passed_different_result`
- Example source queries from this run:
  - `neo4j__13156__q003` / `case_49e42eb3158a`
  - `neo4j__13201__q001` / `case_672a135e71e8`
  - `neo4j__13201__q002` / `case_b418095890c8`
- Additional feature-only random run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_2026_04_0_arcadedb_26_4_2_feature_only_random_20260512b`
- Additional feature-only random seed query id: `ArcadeData__arcadedb__3267__q001`
- Additional feature-only random seed case id: `case_fad01c162280`
