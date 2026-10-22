done
**Suggested title**
`Relationship MERGE may collapse repeated upstream rows down to one row`

**FalkorDB version**
Observed on:

- FalkorDB `4.18.1`

Docker image used locally:
- `falkordb/falkordb:latest`

**Discovered on**
- `2026-04-24`

**Environment**
- Host OS: Windows
- Architecture: x86_64
- Deployment: Docker
- Connection method: RESP / `GRAPH.QUERY`
- Client used for direct reproduction: repo executor + direct `GRAPH.QUERY`
- Differential comparison target: Neo4j Community `2026.02.2`

**Describe the bug**
FalkorDB may collapse repeated upstream rows down to a single row when a later clause performs relationship `MERGE`.

In the minimized repro below, the upstream pipeline intentionally contains three identical rows for the same `(personName, company)` pair. Neo4j preserves the three rows through the relationship `MERGE`, so both `COUNT(*)` and `COUNT(r)` return `3`.

FalkorDB instead collapses the pipeline down to one row and returns `1` for both counts.

This does not look like a general aggregation issue, because the same repeated-row shape works on both engines without the later relationship `MERGE`. It also does not look like a general `MERGE` issue, because node-only `MERGE` preserves the three rows on both engines.

The boundary looks much narrower: repeated upstream rows are being incorrectly deduplicated specifically by relationship `MERGE`.

**To Reproduce**
1. Start the FalkorDB Docker container.
2. Run setup:

```cypher
CREATE (:Person {name:'Alice', age:30}),
       (:Person {name:'Charlie', age:35}),
       (:Company {name:'TechCorp', industry:'Technology'});
```

3. Run:

```cypher
UNWIND [1, 2, 3] AS i
WITH 'Alice' AS personName, 30 AS personAge, i
MATCH (c:Company)
WHERE c.industry = 'Technology'
MERGE (p:Person {name: personName})-[r:WORKS_AT]->(c)
RETURN personName,
       personAge,
       c.name AS companyName,
       COUNT(*) AS rowCount,
       COUNT(r) AS relationshipCount;
```

**Expected behavior**
Neo4j returns:

```text
Alice, 30, TechCorp, 3, 3
```

FalkorDB should preserve the same three repeated rows through the `MERGE`, so it should return the same counts.

**Actual behavior**
FalkorDB returns:

```text
Alice, 30, TechCorp, 1, 1
```

So the three repeated upstream rows are collapsed to one row.

**Control query 1**
If the later phase is read-only, both engines preserve the repeated rows:

```cypher
UNWIND [1, 2, 3] AS i
WITH 'Alice' AS personName, 30 AS personAge, i
MATCH (c:Company)
WHERE c.industry = 'Technology'
MATCH (p:Person {name: personName})
RETURN personName,
       personAge,
       c.name AS companyName,
       COUNT(*) AS rowCount;
```

Observed result on both Neo4j and FalkorDB:

```text
Alice, 30, TechCorp, 3
```

So the repeated upstream rows are not inherently unstable.

**Control query 2**
If the later `MERGE` is node-only, both engines also preserve the repeated rows:

```cypher
UNWIND [1, 2, 3] AS i
WITH 'Alice' AS personName, 30 AS personAge, i
MATCH (c:Company)
WHERE c.industry = 'Technology'
MERGE (p:Person {name: personName})
RETURN personName,
       personAge,
       c.name AS companyName,
       COUNT(*) AS rowCount;
```

Observed result on both Neo4j and FalkorDB:

```text
Alice, 30, TechCorp, 3
```

So `MERGE` in general is not the problem.

**Control query 3**
If the later write uses relationship `CREATE` instead of relationship `MERGE`, both engines again preserve the repeated rows:

```cypher
UNWIND [1, 2, 3] AS i
WITH 'Alice' AS personName, 30 AS personAge, i
MATCH (c:Company)
WHERE c.industry = 'Technology'
MATCH (p:Person {name: personName})
CREATE (p)-[r:WORKS_AT]->(c)
RETURN personName,
       personAge,
       c.name AS companyName,
       COUNT(r) AS relationshipCount;
```

Observed result on both Neo4j and FalkorDB:

```text
Alice, 30, TechCorp, 3
```

This makes the boundary much clearer: the bad behavior is tied specifically to relationship `MERGE`, not to repeated rows, writes, or aggregation in general.

**Stronger reproducer**
The original differential seed also reproduces the same issue in a larger query:

```cypher
UNWIND [1, 2, 3] AS i
CALL {
  WITH i
  MATCH (p:Person)
  WHERE p.age > 25
  RETURN p.name AS personName, p.age AS personAge
}
WITH personName, personAge, i
MATCH (c:Company)
WHERE c.industry = 'Technology'
MERGE (p:Person {name: personName})-[r:WORKS_AT]->(c)
RETURN personName,
       personAge,
       c.name AS companyName,
       COUNT(r) AS relationshipCount
ORDER BY personName DESC
LIMIT 5;
```

Observed Neo4j result:

```text
Charlie, 35, TechCorp, 3
Alice, 30, TechCorp, 3
```

Observed FalkorDB result:

```text
Charlie, 35, TechCorp, 1
Alice, 30, TechCorp, 1
```

So the same row-collapse problem also appears when the repeated upstream rows come from a subquery, not just from a literal `UNWIND`.

**Differential seed**
This family was first noticed in:

- `RedisGraph__RedisGraph__3010__q001`

**Verification Environment**
Plain FalkorDB Docker image is enough.

Preferred verification target:
- Docker image
- x86_64
- `falkordb/falkordb`

I can verify a fix using:
- plain FalkorDB Docker image on Windows host
- direct `GRAPH.QUERY`
