done
**Suggested title**
`Anonymous pattern predicates after WITH may fail with filtered-alias errors`

**FalkorDB version**
Observed on:

- FalkorDB `4.18.2`
- FalkorDB `4.18.4`

Docker images used locally:
- `falkordb/falkordb:v4.18.2`
- `falkordb/falkordb:v4.18.4`

**Discovered on**
- `2026-05-01`

**Environment**
- Host OS: Windows
- Architecture: x86_64
- Deployment: Docker
- Connection method: RESP / `GRAPH.QUERY`
- Client used for direct reproduction: repo executor + direct RESP execution
- Differential comparison target: Neo4j Community `2026.02.2`

**Describe the bug**
FalkorDB may fail when a `WHERE` clause that appears after `WITH` uses an anonymous pattern predicate such as:

```cypher
()<-[:KNOWS {since: 2020}]-()
```

In the minimized repro below, the anonymous pattern is uncorrelated: it simply tests whether any matching relationship exists in the graph. Since the setup graph does contain such a relationship, both rows should be preserved.

Neo4j returns both `Alice` and `Bob`.
FalkorDB instead fails with:

```text
Unable to resolve filtered alias '@anon_2'
```

This does not look like a generic pattern-predicate problem, because the same anonymous predicate works on FalkorDB when used directly in the original `MATCH ... WHERE ...` clause without the intermediate `WITH`.

**To Reproduce**
1. Start the FalkorDB Docker container.
2. Run:

```cypher
CREATE (:Person {name:'Alice', age:30})-[:KNOWS {since:2020}]->(:Person {name:'Bob', age:25});

MATCH (n:Person)
WHERE n.age > 20
WITH n
WHERE ()<-[:KNOWS {since: 2020}]-()
RETURN n.name AS name
ORDER BY n.name;
```

**Expected behavior**
Observed Neo4j result:

```text
Alice
Bob
```

FalkorDB should return the same two rows.

**Actual behavior**
Observed FalkorDB result:

```text
Unable to resolve filtered alias '@anon_2'
```

So the anonymous pattern predicate is accepted syntactically, but later filtered-alias resolution fails during execution.

**Control query 1**
If the same anonymous pattern predicate is used directly in the original `MATCH ... WHERE ...` clause, both engines behave correctly:

```cypher
CREATE (:Person {name:'Alice', age:30})-[:KNOWS {since:2020}]->(:Person {name:'Bob', age:25});

MATCH (n:Person)
WHERE n.age > 20 AND ()<-[:KNOWS {since: 2020}]-()
RETURN n.name AS name
ORDER BY n.name;
```

Observed result on both Neo4j and FalkorDB:

```text
Alice
Bob
```

So the anonymous pattern predicate itself is not enough to trigger the issue.

**Control query 2**
Replacing the post-`WITH` predicate with a literal boolean also works on both engines:

```cypher
CREATE (:Person {name:'Alice', age:30})-[:KNOWS {since:2020}]->(:Person {name:'Bob', age:25});

MATCH (n:Person)
WHERE n.age > 20
WITH n
WHERE true
RETURN n.name AS name
ORDER BY n.name;
```

Observed result on both Neo4j and FalkorDB:

```text
Alice
Bob
```

So the problem is not caused by `WITH` followed by `WHERE` in general.

**Control query 3**
The same family also appears if the post-`WITH` predicate is correlated to the carried variable:

```cypher
CREATE (:Person {name:'Alice', age:30})-[:KNOWS {since:2020}]->(:Person {name:'Bob', age:25});

MATCH (n:Person)
WHERE n.age > 20
WITH n
WHERE (n)<-[:KNOWS {since: 2020}]-()
RETURN n.name AS name
ORDER BY n.name;
```

Observed results:

- Neo4j: `Bob`
- FalkorDB: `Unable to resolve filtered alias '@anon_1'`

So the problem is not limited to fully uncorrelated anonymous patterns. It appears to affect anonymous pattern predicates after `WITH` more generally.

**Control query 4**
Wrapping the same anonymous post-`WITH` predicate in `EXISTS(...)` does not avoid the problem on FalkorDB:

```cypher
CREATE (:Person {name:'Alice', age:30})-[:KNOWS {since:2020}]->(:Person {name:'Bob', age:25});

MATCH (n:Person)
WHERE n.age > 20
WITH n
WHERE EXISTS(()<-[:KNOWS {since: 2020}]-())
RETURN n.name AS name
ORDER BY n.name;
```

Observed behavior:

- Neo4j returns `Alice`, `Bob`
- FalkorDB fails with:

```text
_AR_EXP_UpdateEntityIdx: Unable to locate a value with alias ()<-[:KNOWS {since: 2020}]-() within the record
```

This suggests the deeper issue is still post-`WITH` handling of anonymous pattern predicates, even when expressed through `EXISTS(...)`.

**Differential seed**
This family surfaced while minimizing:

- `FalkorDB__FalkorDB__1318__q001`

**Verification Environment**
Plain FalkorDB Docker image is enough.

Preferred verification target:
- Docker image
- x86_64
- `falkordb/falkordb:v4.18.2`

I can verify a fix using:
- plain FalkorDB Docker image on Windows host
- project FalkorDB executor / direct RESP execution

**Stage3 provenance**
- Stage3 run: `historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b`
- Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_falkordb_4_18_2_skeleton_20260501`
- Query ID: `FalkorDB__FalkorDB__1318__q001`
- Case ID: `case_727540c351ff`
