done
**Suggested title**
`Variable-length pattern comprehension may duplicate projected elements`

**ArcadeDB version**
Observed on Docker images:

- `arcadedata/arcadedb:26.4.1-SNAPSHOT`
- `arcadedata/arcadedb:26.4.2`

Not reproduced on:

- `arcadedata/arcadedb:26.3.2` for the same minimized query shape

The current moving `latest` image behaved differently again during direct checks, so the stable signal here is the `26.4.x` duplication.

**Discovered on**
- `2026-04-25`

**Environment**
- Host OS: Windows
- Architecture: x86_64
- Deployment: Docker
- ArcadeDB endpoint: HTTP `/api/v1/command/arcade`
- Request mode used for direct reproduction:
  - `language: cypher`
  - `serializer: studio`
- Differential comparison target: Neo4j Docker `neo4j:latest`

**Describe the bug**
ArcadeDB may duplicate results inside a pattern comprehension when the pattern uses a variable-length relationship.

The issue is not with pattern comprehension in general:

- fixed-length pattern comprehension works
- unmatched variable-length pattern comprehension also works
- the duplication appears when a variable-length pattern comprehension actually matches

This suggests the variable-length expansion is feeding duplicate rows into the comprehension result collection.

**To Reproduce**

Setup:

```cypher
CREATE (a:Person {name:'Alice'}),
       (b:Person {name:'Bob'}),
       (c:Person {name:'Charlie'}),
       (a)-[:KNOWS]->(b),
       (b)-[:KNOWS]->(c);
```

Query:

```cypher
MATCH (p1:Person {name:'Alice'}), (p2:Person {name:'Bob'})
RETURN [(p1)-[:KNOWS*1..3]->(p2) | 'X'] AS vals;
```

**Expected behavior**
There is one matching path from `Alice` to `Bob`, so the comprehension should yield one element:

```text
["X"]
```

Observed Neo4j result:

```text
["X"]
```

**Actual behavior**
Observed ArcadeDB `26.4.1-SNAPSHOT` and `26.4.2` result:

```text
["X", "X"]
```

So ArcadeDB duplicates the projected element even though there is only one matching path.

**Control cases**

If the same comprehension uses a fixed-length pattern, ArcadeDB behaves normally:

```cypher
MATCH (p1:Person {name:'Alice'}), (p2:Person {name:'Bob'})
RETURN [(p1)-[:KNOWS]->(p2) | 'X'] AS vals;
```

Observed result on Neo4j and ArcadeDB `26.4.1-SNAPSHOT` / `26.4.2`:

```text
["X"]
```

So pattern comprehension itself is not the issue.

If the variable-length pattern does not match, ArcadeDB also behaves normally:

```cypher
MATCH (p1:Person {name:'Charlie'}), (p2:Person {name:'Bob'})
RETURN [(p1)-[:KNOWS*1..3]->(p2) | 'X'] AS vals;
```

Observed result on Neo4j and ArcadeDB `26.4.1-SNAPSHOT` / `26.4.2`:

```text
[]
```

So the bug is tied to matched variable-length pattern comprehensions, not to the unmatched case.

The projected expression itself does not matter either. Returning a node property shows the same duplication:

```cypher
MATCH (p1:Person {name:'Alice'}), (p2:Person {name:'Bob'})
RETURN [(p1)-[:KNOWS*1..3]->(p2) | p1.name] AS vals;
```

Observed Neo4j result:

```text
["Alice"]
```

Observed ArcadeDB `26.4.1-SNAPSHOT` / `26.4.2` result:

```text
["Alice", "Alice"]
```

This makes the boundary much clearer:

- fixed-length pattern comprehension: correct
- unmatched variable-length pattern comprehension: correct
- matched variable-length pattern comprehension: duplicated result elements

**Stronger reproducer**
The same family also appears inside a larger query without changing the core behavior:

```cypher
MATCH (p1:Person) WHERE p1.age > 25
MATCH (p2:Person) WHERE p2.age < 30
RETURN p1.name AS person1,
       p2.name AS person2,
       [(p1)-[:KNOWS*1..3]->(p2) | p1.name] AS pathNames;
```

Observed Neo4j result:

```text
Alice,   Bob, ["Alice"]
Charlie, Bob, []
```

Observed ArcadeDB `26.4.1-SNAPSHOT` / `26.4.2` result:

```text
Alice,   Bob, ["Alice", "Alice"]
Charlie, Bob, []
```

So this is not limited to the single-row exact-endpoint shape.

**Differential seed**
This family was first surfaced while compacting:

- `ArcadeData__arcadedb__3766__q003`

The original generated query used:

```cypher
[(p1)-[:KNOWS*1..3]->(p2) | p1.name] AS pathNames
```

and ArcadeDB duplicated `pathNames` where Neo4j returned one element.

**Verification environment**
Verified directly against:

- Neo4j Docker container: `neo4j:latest`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.4.1-SNAPSHOT`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.4.2`
- Local differential-testing harness
- Direct ArcadeDB HTTP execution

**Stage3 provenance**
This report predates explicit file-level Stage3 provenance tracking.
The underlying differential-seed source query was instantiated in:

- Stage3 run: `historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b`
- Query ID: `ArcadeData__arcadedb__3766__q003`
- Case ID: `case_c0845923b333`
- Stage3 artifact: `data/batch_runs/historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b/stage3_main/instantiated_cases.jsonl`
