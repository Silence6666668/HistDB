done
**Suggested title**
`MERGE with an unbound label-only endpoint may reuse an existing node instead of creating a new one`

**ArcadeDB version**
Observed on Docker images:

- `arcadedata/arcadedb:latest`
- `arcadedata/arcadedb:26.3.2`
- `arcadedata/arcadedb:26.4.1-SNAPSHOT`
- `arcadedata/arcadedb:26.4.2`

The current local `latest` container contains ArcadeDB `26.4.1-SNAPSHOT` jars.

**Discovered on**
- `2026-04-24`

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
When `MERGE` creates a relationship pattern whose endpoint variable is still unbound and specified only by label, ArcadeDB may incorrectly reuse an already existing node with that label.

In Neo4j, a query like:

```cypher
MERGE (p)-[:WORKS_AT]->(c:Company)
```

with `c` unbound does not mean "match any existing `Company`".
It means the whole pattern should be merged.
If no such pattern exists, Neo4j creates a fresh `Company` node for `c`.

ArcadeDB instead appears to bind `c` to an existing `Company` node, even though `c` was never matched explicitly.

**To Reproduce**

Setup:

```cypher
CREATE (:Person {name:'Alice'}),
       (:Company {name:'TechCorp', industry:'Technology'});
```

Query:

```cypher
MATCH (p:Person {name:'Alice'})
MERGE (p)-[r:WORKS_AT {since: 2020}]->(c:Company)
WITH c
MATCH (company:Company)
RETURN c.name AS merged_company_name,
       c.industry AS merged_industry,
       count(company) AS total_companies;
```

**Expected behavior**
Neo4j creates a new label-only `Company` node for the unbound endpoint `c`.
So the merged endpoint has no properties, and there are now two `Company` nodes total.

Observed Neo4j result:

```text
merged_company_name = null
merged_industry     = null
total_companies     = 2
```

**Actual behavior**
ArcadeDB reuses the already existing `TechCorp` node:

```text
merged_company_name = TechCorp
merged_industry     = Technology
total_companies     = 1
```

So the unbound label-only endpoint is treated as if it matched an arbitrary existing `Company`, rather than being created as part of the merged pattern.

**Control cases**

If no `Company` exists yet, both engines create a fresh label-only endpoint:

```cypher
CREATE (:Person {name:'Alice'});

MATCH (p:Person {name:'Alice'})
MERGE (p)-[r:WORKS_AT {since: 2020}]->(c:Company)
WITH c
MATCH (company:Company)
RETURN c.name AS merged_company_name,
       count(company) AS total_companies;
```

Observed result on Neo4j and ArcadeDB:

```text
merged_company_name = null
total_companies     = 1
```

So ArcadeDB is capable of creating the endpoint when no existing candidate node is present.

If the endpoint is explicitly bound first, both engines correctly reuse it:

```cypher
MATCH (p:Person {name:'Alice'}), (c:Company {name:'TechCorp'})
MERGE (p)-[r:WORKS_AT {since: 2020}]->(c)
WITH c
MATCH (company:Company)
RETURN c.name AS merged_company_name,
       c.industry AS merged_industry,
       count(company) AS total_companies;
```

Observed result on Neo4j and ArcadeDB:

```text
merged_company_name = TechCorp
merged_industry     = Technology
total_companies     = 1
```

So the problem is not with reusing an already matched endpoint.
It is specifically with an endpoint variable that is still unbound inside the `MERGE` pattern.

With multiple existing `Company` nodes, Neo4j still creates a fresh one, while ArcadeDB still reuses an existing one:

```cypher
CREATE (:Person {name:'Alice'}),
       (:Company {name:'TechCorp', industry:'Technology'}),
       (:Company {name:'DataInc', industry:'Analytics'});

MATCH (p:Person {name:'Alice'})
MERGE (p)-[r:WORKS_AT {since: 2020}]->(c:Company)
WITH c
MATCH (company:Company)
RETURN c.name AS merged_company_name,
       c.industry AS merged_industry,
       count(company) AS total_companies;
```

Observed Neo4j result:

```text
merged_company_name = null
merged_industry     = null
total_companies     = 3
```

Observed ArcadeDB result:

```text
merged_company_name = TechCorp
merged_industry     = Technology
total_companies     = 2
```

This makes the boundary much clearer:
- no existing endpoint: both engines create one
- explicitly bound endpoint: both engines reuse it
- unbound label-only endpoint with existing candidates: ArcadeDB reuses an existing node where Neo4j creates a fresh one

**Differential seed**
This family was first surfaced while minimizing an ArcadeDB runtime difference derived from:

- `ArcadeData__arcadedb__3148__q001`

**Verification environment**
Verified directly against:

- Neo4j Docker container: `neo4j:latest`
- ArcadeDB Docker container: `arcadedata/arcadedb:latest`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.3.2`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.4.1-SNAPSHOT`
- ArcadeDB Docker container: `arcadedata/arcadedb:26.4.2`
- Local differential-testing harness
- Direct ArcadeDB HTTP execution using the same request mode as Studio
