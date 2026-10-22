done
**Suggested title**
`Duration ordering comparisons may return boolean instead of null`

**FalkorDB version**
Observed on:

- FalkorDB `4.18.1`
- FalkorDB `4.18.2`
- FalkorDB `4.18.4`

Docker images used for current re-verification:
- `falkordb/falkordb:v4.18.2`
- `falkordb/falkordb:v4.18.4`

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
FalkorDB may evaluate duration ordering comparisons (`>`) to boolean values where Neo4j evaluates the same expressions to `null`.

In the simplest repro below, Neo4j returns:

```text
gt = null
gt2 = null
eq = true
```

for:

```cypher
RETURN duration('P2Y') > duration('P1Y') AS gt,
       duration('P6M') > duration('P1Y') AS gt2,
       duration('P2Y') = duration('P2Y') AS eq;
```

FalkorDB instead returns:

```text
gt  = true
gt2 = false
eq  = true
```

So equality behaves consistently, but ordering comparisons over durations do not.

This difference is large enough to affect filtering. A `WHERE p.duration > duration('P1Y')` predicate that removes all rows on Neo4j can keep rows on FalkorDB.

**To Reproduce**
1. Start the FalkorDB Docker container.
2. Run:

```cypher
RETURN duration('P2Y') > duration('P1Y') AS gt,
       duration('P6M') > duration('P1Y') AS gt2,
       duration('P2Y') = duration('P2Y') AS eq;
```

**Expected behavior**
Observed Neo4j result:

```text
gt  = null
gt2 = null
eq  = true
```

FalkorDB should match that behavior.

**Actual behavior**
Observed FalkorDB result:

```text
gt  = true
gt2 = false
eq  = true
```

The same behavior still reproduces on `falkordb/falkordb:v4.18.2`.

**Control query 1**
Equality comparisons over durations behave normally on both engines:

```cypher
RETURN duration('P2Y') = duration('P2Y') AS eq;
```

Observed result on both Neo4j and FalkorDB:

```text
true
```

So the issue is not that duration literals are unsupported in general. The divergence is specifically in ordering comparisons.

**Control query 2**
The same ordering mismatch appears for several duration shapes, not just year/month durations:

```cypher
RETURN duration('PT2S') > duration('PT1S') AS gt_sec,
       duration('P2D') > duration('P1D') AS gt_day,
       duration('P2M') > duration('P1M') AS gt_month;
```

Observed Neo4j result:

```text
gt_sec   = null
gt_day   = null
gt_month = null
```

Observed FalkorDB result:

```text
gt_sec   = true
gt_day   = true
gt_month = true
```

So this is not limited to one specific duration unit.

**Stronger reproducer**
The mismatch is observable in `WHERE` filtering too:

```cypher
CREATE (:Person {name:'Alice', duration: duration('P2Y')}),
       (:City {name:'London', population: 8900000});

MATCH (p:Person {name: 'Alice'})
MATCH (c:City)
WHERE p.duration > duration('P1Y') AND c.population > 5000000
RETURN p.name AS person_name,
       c.name AS city_name,
       p.duration AS person_duration,
       c.population AS city_population
ORDER BY city_population DESC
LIMIT 2;
```

Observed Neo4j result:

```text
0 rows
```

Observed FalkorDB result:

```text
Alice, London, 63072000, 8900000
```

So the ordering mismatch is not just visible in projected expressions. It can change row survival in `WHERE`.

**Control query 3**
Property equality over durations still works on both engines:

```cypher
CREATE (:Person {name:'Alice', duration: duration('P2Y')});

MATCH (p:Person {name:'Alice'})
WHERE p.duration = duration('P2Y')
RETURN p.name AS name;
```

Observed result on both Neo4j and FalkorDB:

```text
Alice
```

This again narrows the issue to duration ordering comparisons, not duration properties in general.

**Differential seed**
This family was first noticed in:

- `RedisGraph__RedisGraph__98__q001`

**Verification Environment**
Plain FalkorDB Docker image is enough.

Preferred verification target:
- Docker image
- x86_64
- `falkordb/falkordb:v4.18.2`

I can verify a fix using:
- plain FalkorDB Docker image on Windows host
- direct `GRAPH.QUERY`

**Stage3 provenance**
This report predates explicit file-level Stage3 provenance tracking.

- The family was first noticed while minimizing: `RedisGraph__RedisGraph__98__q001`
- The exact original `case_id` was not retained in the report
- Current-version behavior was reverified directly on:
  - FalkorDB Docker image `falkordb/falkordb:v4.18.2`
  - Neo4j Community `2026.02.2`
