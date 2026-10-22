**Suggested title**
`Undirected relationship patterns with type alternation may double-count rows`

**Memgraph version**
Observed on:

- Memgraph `3.9.0`

Docker image used locally:
- `memgraph/memgraph:3.9.0`

**Discovered on**
- `2026-04-30`

**Environment**
- Host OS: Windows
- Architecture: x86_64
- Deployment: Docker
- Connection method: Bolt
- Client used for direct reproduction: repo Stage4 executor using Python `neo4j` Bolt driver
- Differential comparison target: Neo4j Community `2026.02.2`

**Describe the bug**
Memgraph may double-count rows for an undirected relationship pattern when the pattern uses relationship-type alternation such as `:KNOWS|FRIEND`.

In the minimized repro below, the graph has exactly three directed relationships:

- `Alice -[:KNOWS]-> Bob`
- `Alice -[:FRIEND]-> Charlie`
- `Bob -[:KNOWS]-> Charlie`

Neo4j treats the undirected alternation scan as three matched rows and returns:

```text
cnt = 3
distinct_cnt = 3
```

Memgraph returns:

```text
cnt = 6
distinct_cnt = 3
```

So the same three relationships are being counted twice even though `collect(DISTINCT r)` still sees only three distinct relationships.

This does not look like a generic undirected-pattern issue, because plain `:KNOWS` and plain `:FRIEND` behave like Neo4j. The failure starts when the undirected pattern uses a type alternation.

**To Reproduce**
1. Start the Memgraph Docker container.
2. Run:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Node {name: 'Alice'})-[:KNOWS]->(b:Node {name: 'Bob'}),
       (a)-[:FRIEND]->(c:Node {name: 'Charlie'}),
       (b)-[:KNOWS]->(c);

MATCH ()-[r:KNOWS|FRIEND]-()
RETURN count(*) AS cnt,
       size(collect(DISTINCT r)) AS distinct_cnt;
```

**Expected behavior**
Observed Neo4j result:

```text
cnt = 3
distinct_cnt = 3
```

Memgraph should return the same result.

**Actual behavior**
Observed Memgraph result:

```text
cnt = 6
distinct_cnt = 3
```

The three matched relationships are counted twice.

**Control query 1**
If the pattern is directed, both engines behave correctly:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Node {name: 'Alice'})-[:KNOWS]->(b:Node {name: 'Bob'}),
       (a)-[:FRIEND]->(c:Node {name: 'Charlie'}),
       (b)-[:KNOWS]->(c);

MATCH ()-[r:KNOWS|FRIEND]->()
RETURN count(*) AS cnt,
       size(collect(DISTINCT r)) AS distinct_cnt;
```

Observed result on both Neo4j and Memgraph:

```text
cnt = 3
distinct_cnt = 3
```

So type alternation alone is not enough.

**Control query 2**
If the pattern is undirected but uses only a single relationship type, both engines also line up:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Node {name: 'Alice'})-[:KNOWS]->(b:Node {name: 'Bob'}),
       (a)-[:FRIEND]->(c:Node {name: 'Charlie'}),
       (b)-[:KNOWS]->(c);

MATCH ()-[r:KNOWS]-()
RETURN count(*) AS cnt,
       size(collect(DISTINCT r)) AS distinct_cnt;
```

Observed result on both Neo4j and Memgraph:

```text
cnt = 4
distinct_cnt = 2
```

Likewise for:

```cypher
MATCH ()-[r:FRIEND]-()
RETURN count(*) AS cnt,
       size(collect(DISTINCT r)) AS distinct_cnt;
```

Observed result on both engines:

```text
cnt = 2
distinct_cnt = 1
```

So undirected matching by itself is not the issue either.

**Control query 3**
The same double-counting also appears when the alternation scan is nested under an outer row variable:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Node {name: 'Alice'})-[:KNOWS]->(b:Node {name: 'Bob'}),
       (a)-[:FRIEND]->(c:Node {name: 'Charlie'}),
       (b)-[:KNOWS]->(c);

MATCH (n:Node)
OPTIONAL MATCH ()-[r:KNOWS|FRIEND]-()
RETURN n.name AS name,
       count(*) AS cnt,
       size(collect(DISTINCT r)) AS distinct_cnt
ORDER BY name;
```

Observed Neo4j result:

```text
Alice,   3, 3
Bob,     3, 3
Charlie, 3, 3
```

Observed Memgraph result:

```text
Alice,   6, 3
Bob,     6, 3
Charlie, 6, 3
```

So the row multiplier leaks into grouped aggregation too.

**Why this looks like a real bug**
The boundary is narrow:

- directed `:KNOWS|FRIEND` works
- undirected single-type patterns work
- only undirected type alternation is overcounted

That makes this look like a specific execution bug in Memgraph's handling of undirected relationship-type alternation, not a generic Cypher semantic difference.

**Differential seed**
This family surfaced while minimizing:

- `RedisGraph__RedisGraph__1346__q005`

**Verification Environment**
Plain Memgraph Docker image is enough.

Preferred verification target:
- Docker image
- x86_64
- `memgraph/memgraph`

I can verify a fix using:
- plain Memgraph Docker image on Windows host
- repo Stage4 executor / direct Bolt execution
