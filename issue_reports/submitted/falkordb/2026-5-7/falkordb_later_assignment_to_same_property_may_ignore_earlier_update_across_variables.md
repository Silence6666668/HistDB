done
**Suggested title**
`Later assignment to the same property may ignore an earlier update when separated by another variable assignment`

**FalkorDB version**
Observed on:

- FalkorDB `4.18.2`
- FalkorDB `4.18.3`
- FalkorDB `4.18.4`

Docker images used locally:
- `falkordb/falkordb:v4.18.2`
- `falkordb/falkordb:v4.18.3`
- `falkordb/falkordb:v4.18.4`

**Discovered on**
- `2026-05-07`

**Environment**
- Host OS: Windows
- Architecture: x86_64
- Deployment: Docker
- Connection method: RESP / `GRAPH.QUERY`
- Client used for direct reproduction: repo executor + direct RESP execution
- Differential comparison target: Neo4j Community

**Describe the bug**
FalkorDB may evaluate a later assignment to a property against a stale value when the same property is assigned earlier in the same clause and an assignment to another variable appears in between.

In the minimized repro below, the query updates `a.age`, then touches `b.age`, then updates `a.age` again:

```cypher
SET a.age = a.age + 1,
    b.age = b.age,
    a.age = a.age + 2
```

Neo4j treats the later `a.age` assignment as seeing the earlier update, so `Alice` ends at age `33`.

FalkorDB instead returns age `32`, which looks as if the final `a.age = a.age + 2` was evaluated from the original age `30` instead of the already-updated age `31`.

So this does not look like a general repeated-assignment problem. The failure starts when the two assignments to `a.age` are separated by an assignment to a different variable.

**To Reproduce**
1. Start the FalkorDB Docker container.
2. Run:

```cypher
MATCH (n) DETACH DELETE n;

CREATE (a:Person {name: 'Alice', age: 30}),
       (b:Person {name: 'Bob', age: 25});

MATCH (a:Person {name: 'Alice'}), (b:Person {name: 'Bob'})
SET a.age = a.age + 1,
    b.age = b.age,
    a.age = a.age + 2
RETURN a.age AS aliceAge, b.age AS bobAge;
```

**Expected behavior**
Observed Neo4j result:

```text
aliceAge = 33
bobAge   = 25
```

FalkorDB should return the same row.

**Actual behavior**
Observed FalkorDB `4.18.2` / `4.18.3` result:

```text
aliceAge = 32
bobAge   = 25
```

So the later assignment to `a.age` does not appear to see the earlier `a.age = a.age + 1`.

**Control query 1**
Repeated assignment to the same property is not broken by itself. If there is no assignment to another variable in between, both engines behave correctly:

```cypher
MATCH (n) DETACH DELETE n;

CREATE (a:Person {name: 'Alice', age: 30}),
       (b:Person {name: 'Bob', age: 25});

MATCH (a:Person {name: 'Alice'}), (b:Person {name: 'Bob'})
SET a.age = a.age + 1,
    a.age = a.age + 2
RETURN a.age AS aliceAge;
```

Observed result on Neo4j and FalkorDB:

```text
aliceAge = 32
```

So the issue is not simply "same property assigned twice in one clause".

**Control query 2**
If the middle assignment stays on the same variable instead of switching to another variable, both engines also line up:

```cypher
MATCH (n) DETACH DELETE n;

CREATE (a:Person {name: 'Alice', age: 30});

MATCH (a:Person {name: 'Alice'})
SET a.age = a.age + 1,
    a.name = 'Alice',
    a.age = a.age + 2
RETURN a.age AS aliceAge, a.name AS name;
```

Observed result on Neo4j and FalkorDB:

```text
aliceAge = 32
name     = Alice
```

So the bad boundary is narrower: the stale-value effect starts when the repeated `a.age` assignments are separated by an assignment to a different variable.

**Control query 3**
The same family also appears in `ON CREATE SET`, so the issue is not limited to plain `SET`:

```cypher
MATCH (n) DETACH DELETE n;

CREATE (a:Person {name: 'Alice', age: 30}),
       (b:Person {name: 'Bob', age: 25});

MATCH (a:Person {name: 'Alice'}), (b:Person {name: 'Bob'})
MERGE (a)-[:KNOWS]->(b)
ON CREATE SET a.age = a.age + 1,
              b.age = b.age + 1,
              a.age = a.age + 2
RETURN a.age AS aliceAge, b.age AS bobAge;
```

Observed results:

- Neo4j:

```text
aliceAge = 33
bobAge   = 26
```

- FalkorDB `4.18.2` / `4.18.3`:

```text
aliceAge = 32
bobAge   = 26
```

So the problem is more general than one specific clause form. A later assignment to `a.age` can miss the earlier update in both plain `SET` and `ON CREATE SET` when another variable assignment is interleaved.

**Why this looks like a real bug**
The boundary is quite clean:

- repeated assignment to the same property alone: OK
- separator assignment on the same variable: OK
- separator assignment on another variable: wrong

That makes this look like an evaluation-order or stale-value bug in FalkorDB's handling of multi-assignment clauses across variables, not a generic expression limitation.

**Differential seed**
This family surfaced while minimizing:

- `FalkorDB__FalkorDB__1248__q001`

Original generated query:

```cypher
MATCH (a:Person {name: 'Alice'}), (b:Person {name: 'Bob'})
FOREACH (x IN [1, 2, 3] |
  MERGE (a)-[:KNOWS]->(b)
  ON CREATE SET a.age = a.age + x,
                b.age = b.age + x,
                a.age = a.age + 1
)
RETURN a.age, b.age
```

This reduces to the same root issue without needing `FOREACH`.

**Verification Environment**
Verified directly against:

- Neo4j Docker container
- FalkorDB Docker container `v4.18.2`
- FalkorDB Docker container `v4.18.3`
- Local differential-testing harness
- Direct FalkorDB RESP execution

**Stage3 provenance**
- Stage3 run: `historical_bug_queries_parallel_llm_v1_feature_only_random_20260506`
- Stage3 artifact: `data/batch_runs/historical_bug_queries_parallel_llm_v1_feature_only_random_20260506/stage3x10/stage3_main/instantiated_cases.jsonl`
- Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_falkordb_4_18_3_feature_only_random_20260507`
- Differential bucket: `all_passed_different_result`
- Source query:
  - `FalkorDB__FalkorDB__1248__q001` / `case_d5605f623d94`
