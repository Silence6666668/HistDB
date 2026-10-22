done
**Suggested title**
`reduce(...) over a null list may return the initial accumulator instead of null`

**ArcadeDB version**
Observed on Docker images:

- `arcadedata/arcadedb:26.3.2`
- `arcadedata/arcadedb:26.4.1-SNAPSHOT`
- `arcadedata/arcadedb:26.4.2`

**Environment**
- Host OS: Windows 10
- Architecture: x86_64
- Deployment: Docker
- ArcadeDB endpoint: HTTP `/api/v1/command/arcade`
- Request mode matches ArcadeDB Studio:
  - `language: opencypher`
  - `serializer: studio`
- Differential comparison target: Neo4j Docker `neo4j:latest`

**Describe the bug**
ArcadeDB may evaluate `reduce(...)` over a `null` list as if the list were empty.

In Neo4j, a `null` list operand makes the whole `reduce(...)` expression evaluate to `null`.
ArcadeDB instead returns the initial accumulator value.

So the engine is not distinguishing:

- an empty list, and
- a `null` list

inside `reduce(...)`.

**To Reproduce**

Query:

```cypher
WITH null AS xs
RETURN reduce(s = 0, x IN xs | s + x) AS total;
```

**Expected behavior**
Observed Neo4j result:

```text
null
```

ArcadeDB should return the same `null` value.

**Actual behavior**
Observed ArcadeDB result on all tested versions:

```text
0
```

So ArcadeDB behaves as if `xs` were `[]` instead of `null`.

**Control cases**

An actual empty list behaves correctly on both engines:

```cypher
WITH [] AS xs
RETURN reduce(s = 0, x IN xs | s + x) AS total;
```

Observed result on Neo4j and all tested ArcadeDB versions:

```text
0
```

A normal non-empty list also behaves correctly:

```cypher
WITH [1, 2, 3] AS xs
RETURN reduce(s = 0, x IN xs | s + x) AS total;
```

Observed result on Neo4j and all tested ArcadeDB versions:

```text
6
```

So this is not a general `reduce(...)` bug.
The failure starts specifically when the list operand is `null`.

**Optional-path variant**

The same bug also appears when the `null` list is produced indirectly from a missing optional path:

Setup:

```cypher
CREATE (:Node {name:'B', value: 2});
```

Query:

```cypher
OPTIONAL MATCH p = (:Node {name:'A'})-[*1..3]->(end:Node)
WITH nodes(p) AS ns
RETURN reduce(s = 0, n IN ns | s + n.value) AS total;
```

Observed results:

- Neo4j:

```text
null
```

- ArcadeDB `latest / 26.3.2 / 26.4.1-SNAPSHOT / 26.4.2`:

```text
0
```

This is not a general null-path projection issue.
For the same missing path, both engines still return `null` for:

```cypher
OPTIONAL MATCH p = (:Node {name:'A'})-[*1..3]->(end:Node)
RETURN length(p) AS hops;
```

Observed result on Neo4j and all tested ArcadeDB versions:

```text
null
```

So the bad behavior starts specifically when the `null` list flows into `reduce(...)`.

**Original differential variant**

This family first resurfaced in the current feature-only random differential in a richer optional-match pipeline:

```cypher
OPTIONAL MATCH p = (start:Node {name: 'A'})-[*1..3]->(end:Node)
WITH start, end, length(p) AS pathLen, reduce(s = 0, n IN nodes(p) | s + n.value) AS totalValue
OPTIONAL MATCH (end)-[r]->(next:Node)
WITH start, end, pathLen, totalValue, count(r) AS outgoingRels, collect(next.name) AS nextNodes
RETURN 1 AS constant, start.name AS startName, end.name AS endName, pathLen, totalValue, outgoingRels, nextNodes;
```

Observed results:

- Neo4j:

```text
constant=1, startName=null, endName=null, pathLen=null, totalValue=null, outgoingRels=0, nextNodes=[]
```

- ArcadeDB `26.4.2`:

```text
constant=1, startName=null, endName=null, pathLen=null, totalValue=0, outgoingRels=0, nextNodes=[]
```

Direct minimization shows that the later optional match is not required.
The smaller root cause is simply `reduce(...)` over a `null` list.

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
  - `ArcadeData__arcadedb__3132__q001` / `case_c18f701063ce`
