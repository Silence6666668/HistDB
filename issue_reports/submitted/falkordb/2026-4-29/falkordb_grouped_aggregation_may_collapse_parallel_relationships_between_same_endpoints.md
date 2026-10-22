done
**Suggested title**
`Grouped aggregation may collapse parallel relationships between the same endpoints`

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
- Client used for direct reproduction: direct `redis-cli GRAPH.QUERY`
- Differential comparison target: Neo4j Community `2026.02.2`

**Describe the bug**
FalkorDB may collapse multiple parallel relationships between the same two nodes down to a single row when the query performs aggregation grouped by one endpoint node.

In the minimized repro below, there are three separate `[:WORKS_FOR]` relationships from the same person to the same company.

Neo4j preserves all three matching rows during aggregation:

- `count(*) = 3`
- `count(p) = 3`
- `collect(p.name) = ["Alice", "Alice", "Alice"]`

FalkorDB instead behaves as if only one row exists:

- `count(*) = 1`
- `count(p) = 1`
- `collect(p.name) = [Alice]`

This suggests FalkorDB is collapsing repeated matches by endpoint pair during grouped aggregation, even though the matched relationships are distinct.

**To Reproduce**
1. Start the FalkorDB Docker container.
2. Run:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (p:Person {name: 'Alice', age: 30}),
       (c:Company {name: 'TechCorp', industry: 'Technology'}),
       (p)-[:WORKS_FOR]->(c),
       (p)-[:WORKS_FOR]->(c),
       (p)-[:WORKS_FOR]->(c);

MATCH (c:Company {name: 'TechCorp'})<-[r:WORKS_FOR]-(p:Person)
WITH c, count(*) AS cstar, count(p) AS cp, collect(p.name) AS names
RETURN c.name AS company_name, cstar, cp, names;
```

**Expected behavior**
Neo4j returns:

```text
TechCorp, 3, 3, ["Alice", "Alice", "Alice"]
```

FalkorDB should return the same values.

**Actual behavior**
FalkorDB returns:

```text
TechCorp, 1, 1, [Alice]
```

So FalkorDB collapses three distinct matching relationships into one grouped row.

**Control query 1**
The relationships are not missing in general. Without grouping, both engines count them correctly:

```cypher
MATCH (c:Company {name: 'TechCorp'})<-[r:WORKS_FOR]-(p:Person)
RETURN count(*) AS cstar, count(p) AS cp, count(r) AS cr, count(DISTINCT p) AS cdp;
```

Observed result on both Neo4j and FalkorDB:

```text
3, 3, 3, 1
```

So the bug is not that the relationships were never created. The collapse happens specifically in the grouped aggregation shape.

**Control query 2**
If the company still receives three incoming `[:WORKS_FOR]` relationships, but from three different people instead of the same person repeated, FalkorDB behaves normally:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (c:Company {name:'TechCorp'}),
       (:Person {name:'Alice'})-[:WORKS_FOR]->(c),
       (:Person {name:'Bob'})-[:WORKS_FOR]->(c),
       (:Person {name:'Charlie'})-[:WORKS_FOR]->(c);

MATCH (c:Company {name: 'TechCorp'})<-[r:WORKS_FOR]-(p:Person)
WITH c, count(*) AS cstar, count(p) AS cp, collect(p.name) AS names
RETURN c.name AS company_name, cstar, cp, names;
```

Observed result on both Neo4j and FalkorDB:

```text
TechCorp, 3, 3, [...]
```

The only meaningful difference from the failing case is that the three rows now come from three different source nodes instead of three parallel relationships between the same two endpoints.

This makes the boundary much sharper: the collapse is tied to repeated parallel edges between the same endpoints.

**Original differential seed**
This family first surfaced in:

- `ArcadeData__arcadedb__3114__q004`

That original query also contained `CALL db.labels()`, but direct reduction shows the divergence already exists before the procedure call. The root issue is the grouped aggregation itself.

**Verification Environment**
Plain FalkorDB Docker image is enough.

Preferred verification target:
- Docker image
- x86_64
- `falkordb/falkordb`

I can verify a fix using:
- plain FalkorDB Docker image on Windows host
- direct `redis-cli GRAPH.QUERY`
