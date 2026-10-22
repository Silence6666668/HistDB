done
**Suggested title**
`OPTIONAL MATCH after a prior relationship traversal may duplicate rows`

**FalkorDB version**
Observed on:

- FalkorDB `4.18.1`

Docker image used locally:
- `falkordb/falkordb:latest`

**Discovered on**
- `2026-04-23`

**Environment**
- Host OS: Windows
- Architecture: x86_64
- Deployment: Docker
- Connection method: RESP / `GRAPH.QUERY`
- Client used for direct reproduction: direct `GRAPH.QUERY`
- Differential comparison target: Neo4j Community `2026.02.2`

**Describe the bug**
FalkorDB may duplicate rows when an `OPTIONAL MATCH` follows a prior relationship traversal and the optional pattern actually matches.

In the minimized repro below, there is only one path:

```text
(:Person {name:'Alice'})-[:WORKS_AT]->(:Company {name:'TechCorp'})-[:LOCATED_IN]->(:City {name:'Metropolis'})
```

Neo4j returns one row, as expected.
FalkorDB returns the same row twice.

This suggests FalkorDB may incorrectly multiply rows when a later `OPTIONAL MATCH` expands from a node that was already reached through an earlier traversal in the same query.

**To Reproduce**
1. Start the FalkorDB Docker container.
2. Run:

```cypher
CREATE (:Person {name:'Alice', age:30})-[:WORKS_AT]->(:Company {name:'TechCorp'})-[:LOCATED_IN]->(:City {name:'Metropolis'});

MATCH (p:Person {age: 30})-[:WORKS_AT]->(c:Company)
OPTIONAL MATCH (c)-[:LOCATED_IN]->(city:City)
RETURN p.name AS person_name, c.name AS company_name, city.name AS city_name
LIMIT 3;
```

**Expected behavior**
Neo4j returns one row:

```text
Alice, TechCorp, Metropolis
```

FalkorDB should return the same single row.

**Actual behavior**
FalkorDB returns two identical rows:

```text
Alice, TechCorp, Metropolis
Alice, TechCorp, Metropolis
```

**Control query 1**
If the optional pattern does not match, both engines return a single row:

```cypher
CREATE (:Person {name:'Alice', age:30})-[:WORKS_AT]->(:Company {name:'TechCorp'});

MATCH (p:Person {age: 30})-[:WORKS_AT]->(c:Company)
OPTIONAL MATCH (c)-[:LOCATED_IN]->(city:City)
RETURN p.name AS person_name, c.name AS company_name, city.name AS city_name
LIMIT 3;
```

Observed result on both Neo4j and FalkorDB:

```text
Alice, TechCorp, null
```

So the issue is not that `OPTIONAL MATCH` always duplicates rows; it appears tied to the optional branch actually finding a match.

**Control query 2**
If the query starts directly from the company node, `OPTIONAL MATCH` behaves normally on both engines:

```cypher
CREATE (:Company {name:'TechCorp'})-[:LOCATED_IN]->(:City {name:'Metropolis'});

MATCH (c:Company {name:'TechCorp'})
OPTIONAL MATCH (c)-[:LOCATED_IN]->(city:City)
RETURN c.name AS company_name, city.name AS city_name;
```

Observed result on both Neo4j and FalkorDB:

```text
TechCorp, Metropolis
```

So the problem is not a general failure of `OPTIONAL MATCH (c)-[:LOCATED_IN]->(city)`. It appears tied to doing that optional expansion after `c` was already reached through an earlier relationship traversal.

**Control query 3**
Without the later `OPTIONAL MATCH`, the earlier traversal alone returns one row on both engines:

```cypher
CREATE (:Person {name:'Alice', age:30})-[:WORKS_AT]->(:Company {name:'TechCorp'})-[:LOCATED_IN]->(:City {name:'Metropolis'});

MATCH (p:Person {age: 30})-[:WORKS_AT]->(c:Company)
RETURN p.name AS person_name, c.name AS company_name;
```

Observed result on both Neo4j and FalkorDB:

```text
Alice, TechCorp
```

This makes the boundary clearer: the duplication appears specifically when the later optional traversal is added after the earlier non-optional traversal.

**Differential seed**
This family was first noticed in:

- `RedisGraph__RedisGraph__1752__q001`

**Verification Environment**
Plain FalkorDB Docker image is enough.

Preferred verification target:
- Docker image
- x86_64
- `falkordb/falkordb`

I can verify a fix using:
- plain FalkorDB Docker image on Windows host
- direct `GRAPH.QUERY`
