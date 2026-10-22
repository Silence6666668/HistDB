done
**Suggested title**
`MATCH` on multiple carried variables after `WITH` crashes the server

**FalkorDB version**
Observed on:

- FalkorDB `4.18.1`
- FalkorDB `4.18.2`

Docker image used for current re-verification:
- `falkordb/falkordb:v4.18.2`

**Discovered on**
- `2026-04-23`

**Environment**
- Host OS: Windows
- Architecture: x86_64
- Deployment: Docker
- Connection method: RESP / `GRAPH.QUERY`
- Client used for direct reproduction: project FalkorDB executor
- Differential comparison target: Neo4j Community `2026.02.2`

**Describe the bug**
FalkorDB can crash when a query:

1. binds multiple variables
2. carries them through `WITH`
3. then re-matches more than one of those bound variables in a later `MATCH`

The minimal repro below returns normally on Neo4j, but on FalkorDB the connection is closed unexpectedly and the server process exits with code `139`.

The container logs include the Redis/FalkorDB crash report with:

```text
main thread terminated
```

so this is not just a query error or timeout; it is a server crash.

**To Reproduce**
1. Start the FalkorDB Docker container.
2. Run:

```cypher
CREATE (a:Person {name:'Alice'}), (b:Person {name:'Bob'})
WITH a, b
MATCH (a), (b)
RETURN a.name AS name, b.name AS name2;
```

**Expected behavior**
Neo4j returns:

```text
Alice, Bob
```

FalkorDB should return the same row and remain healthy.

**Actual behavior**
On FalkorDB:

- the connection is closed unexpectedly
- the container exits
- `docker inspect` reports exit code `139`

Observed client-side error:

```text
FalkorDB connection closed unexpectedly.
```

Observed container-side symptom:

```text
main thread terminated
```

**Control query 1**
Re-matching a single carried variable works:

```cypher
CREATE (a:Person {name:'Alice'})
WITH a
MATCH (a)
RETURN a.name AS name;
```

Observed result on both Neo4j and FalkorDB:

```text
Alice
```

**Control query 2**
Even with two carried variables, re-matching only one of them still works:

```cypher
CREATE (a:Person {name:'Alice'}), (b:Person {name:'Bob'})
WITH a, b
MATCH (a)
RETURN a.name AS name;
```

Observed result on both Neo4j and FalkorDB:

```text
Alice
```

**Control query 3**
The same boundary also holds when the variables were matched from existing nodes rather than freshly created:

```cypher
MATCH (p:Person {name:'Alice'}), (c:Company)
WHERE c.industry='Technology'
WITH p, c
MATCH (p)
RETURN p.name AS person_name, c.name AS company_name;
```

Observed FalkorDB result:

```text
Alice, TechCorp
```

But re-matching both carried variables crashes the server:

```cypher
MATCH (p:Person {name:'Alice'}), (c:Company)
WHERE c.industry='Technology'
WITH p, c
MATCH (p), (c)
RETURN p.name AS person_name, c.name AS company_name;
```

So the boundary is not tied to fresh creation specifically. It is tied to a later `MATCH` that reintroduces multiple carried variables together.

**Additional supporting reproducer**
The same crash also appears when the query performs a write first and then re-matches both carried variables together:

```cypher
MATCH (p:Person {name: 'Alice'}), (c:Company)
WHERE c.industry = 'Technology'
CREATE (p)-[:WORKS_AT]->(c)
WITH p, c
MATCH (p), (c)
RETURN p.name AS person_name,
       c.name AS company_name,
       COUNT(*) AS total_relationships;
```

Observed Neo4j result:

```text
Alice, TechCorp, 1
```

Observed FalkorDB behavior:

- connection closed unexpectedly
- container exits with code `139`

So the family is not limited to pure read-after-`WITH`; it also survives a preceding write, as long as the later `MATCH` reintroduces multiple carried variables together.

**Stronger reproducer**
A slightly larger write pattern also crashes FalkorDB:

```cypher
CREATE (a:Person {name: 'Alice', hobbies: ['reading', 'cycling']}),
       (b:Person:Employee {name: 'Bob'}),
       (c:Person {name: 'Charlie'})
WITH a, b, c
MATCH (a), (b)
CREATE (a)-[:KNOWS]->(b)
WITH a, b, c
MATCH (b), (c)
CREATE (b)-[:WORKS_WITH]->(c)
RETURN a.name AS person1, b.name AS person2, c.name AS person3;
```

Observed Neo4j result:

```text
Alice, Bob, Charlie
```

Observed FalkorDB behavior:

- connection closed unexpectedly
- server exits with code `139`

**Additional supporting reproducer 2**
The same crash family also shows up in a longer query that continues past the later re-match into more writes:

```cypher
MATCH (p1:Person), (p2:Person)
WHERE p1.name = 'Alice' AND p2.name = 'Bob'
CREATE (p1)-[:FRIEND]->(p2)
WITH p1, p2
MATCH (p1), (p2)
CREATE (p1)-[:WORKS_WITH]->(p2)
WITH p1, p2
ORDER BY p1.age DESC
UNWIND [p1, p2] AS person
MERGE (person)-[:KNOWS]->(person)
RETURN person.name, person.age
ORDER BY person.age;
```

Observed Neo4j result:

```text
Bob, 25
Alice, 30
```

Observed FalkorDB behavior:

- connection closed unexpectedly
- container exits with code `139`

The query contains more clauses than the minimal crash, but the same core trigger remains: once multiple carried variables are reintroduced together by a later `MATCH`, FalkorDB can crash before the rest of the query completes.

**Additional supporting reproducer 3 (current version)**
The same crash family also appears when one of the later re-matched variables comes from a subquery result rather than only from the outer scope:

```cypher
CREATE (:Person {name:'Alice'}), (:Person {name:'Bob'});

MATCH (n:Person)
WITH n
CALL {
  WITH n
  RETURN DISTINCT n AS m
}
MATCH (n), (m)
RETURN n.name, m.name;
```

Observed Neo4j result:

```text
Alice, Alice
Bob,   Bob
```

Observed FalkorDB `4.18.2` behavior:

- connection closed unexpectedly
- container exits with code `139`

But if the later clause re-matches only `n` or only `m`, both engines behave normally. So this still fits the same broader boundary: once multiple carried variables are reintroduced together in a later `MATCH`, FalkorDB can crash, even if one of those variables came back from a subquery alias.

**Differential seeds**
This family was first noticed in:

- `FalkorDB__FalkorDB__774__q004`
- `FalkorDB__FalkorDB__898__q001`
- `RedisGraph__RedisGraph__628__q004`
- `RedisGraph__RedisGraph__521__q003`

**Verification Environment**
Plain FalkorDB Docker image is enough.

Preferred verification target:
- Docker image
- x86_64
- `falkordb/falkordb:v4.18.2`

I can verify a fix using:
- plain FalkorDB Docker image on Windows host
- project RESP executor / direct `GRAPH.QUERY`

**Stage3 provenance**
This report predates explicit file-level Stage3 provenance tracking for its original minimization.

- Original differential seeds:
  - `FalkorDB__FalkorDB__774__q004`
  - `FalkorDB__FalkorDB__898__q001`
  - `RedisGraph__RedisGraph__628__q004`
  - `RedisGraph__RedisGraph__521__q003`
- Current-version supporting seed from the `4.18.2` skeleton differential:
  - Stage3 run: `historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b`
  - Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_falkordb_4_18_2_skeleton_20260501`
  - Query ID: `FalkorDB__FalkorDB__1322__q001`
  - Case ID: `case_8f02b1c9d70a`
