done
**Suggested title**
`Aggregate-plus-scalar expressions over empty input may return no rows unless the aggregate is materialized first`

**Memgraph version**
Observed on:

- Memgraph `3.9.0`
- Memgraph `3.10.1` (re-verified on `2026-05-19`)

Docker images used locally:
- `memgraph/memgraph:3.9.0`
- `memgraph/memgraph:latest` (resolved locally to `3.10.1` on `2026-05-19`)

**Discovered on**
- `2026-05-17`

**Environment**
- Host OS: Windows
- Architecture: x86_64
- Deployment: Docker
- Connection method: Bolt
- Client used for direct reproduction: Python `neo4j` Bolt driver
- Differential comparison target: Neo4j Community `2026.04.0`

**Describe the bug**
Memgraph may return `0 rows` when a query has empty input and the `RETURN` clause combines:

- an aggregate such as `count(*)`, and
- a non-literal scalar subexpression such as `abs(...)`, `CASE ...`, or `toInteger(...)`

in the **same inline expression**.

In the minimized repro below, Neo4j returns one row with value `1`. Memgraph instead returns no rows at all.

This does not look like a general problem with aggregates over empty input, because Memgraph returns the correct one-row result for plain `count(*)`, and it also behaves normally if the aggregate is first materialized in `WITH` before combining it with the scalar subexpression.

**To Reproduce**
1. Start the Memgraph Docker container.
2. Run:

```cypher
MATCH (n) DETACH DELETE n;
MATCH (n:MissingLabel)
RETURN count(*) + abs(-1) AS v;
```

**Expected behavior**
Observed Neo4j result:

```text
v = 1
```

Memgraph should return the same single row.

**Actual behavior**
Observed Memgraph result:

```text
0 rows
```

So the row produced by the aggregate over empty input disappears when the aggregate is combined inline with the scalar function call.

**Control query 1**
Plain aggregate over the same empty input behaves correctly on both engines:

```cypher
MATCH (n) DETACH DELETE n;
MATCH (n:MissingLabel)
RETURN count(*) AS c;
```

Observed result on Neo4j and Memgraph:

```text
c = 0
```

So the issue is not simply "aggregate over empty input".

**Control query 2**
If the scalar part is just a literal, both engines also behave correctly:

```cypher
MATCH (n) DETACH DELETE n;
MATCH (n:MissingLabel)
RETURN count(*) + 1 AS v;
```

Observed result on Neo4j and Memgraph:

```text
v = 1
```

This suggests the failure is not triggered by every aggregate-plus-scalar expression, but specifically by non-literal scalar subexpressions.

**Control query 3**
If the aggregate is first materialized in `WITH`, both engines behave correctly again:

```cypher
MATCH (n) DETACH DELETE n;
MATCH (n:MissingLabel)
WITH count(*) AS c
RETURN c + abs(-1) AS v;
```

Observed result on Neo4j and Memgraph:

```text
v = 1
```

This makes the boundary much sharper: the problem is tied to the **inline** form, not to the arithmetic itself.

**Related variants**
The same empty-input row-loss also appears with other non-literal scalar subexpressions:

```cypher
MATCH (n:MissingLabel)
RETURN count(*) + toInteger(3.7) AS v;
```

Neo4j:

```text
v = 3
```

Memgraph:

```text
0 rows
```

and

```cypher
MATCH (n:MissingLabel)
RETURN count(*) + CASE WHEN true THEN 1 ELSE 2 END AS v;
```

Neo4j:

```text
v = 1
```

Memgraph:

```text
0 rows
```

These variants suggest the bug is broader than `abs(...)` specifically.

**Differential seed**
This family was surfaced while minimizing:

- `FalkorDB__FalkorDB__37__q005`
- `case_e8279b2b0853`

The original larger query reduced to the same core effect: when the input became empty before the final aggregate-only `RETURN`, Neo4j still produced one row while Memgraph dropped it after the aggregate was combined inline with another scalar expression.

**Verification environment**
Verified directly against:

- Neo4j Docker container: `neo4j:2026.04.0`
- Memgraph Docker container: `memgraph/memgraph:3.9.0`
- Memgraph Docker container: `memgraph/memgraph:latest` (resolved locally to `3.10.1`)
- local differential-testing harness
- direct fresh reruns over Bolt
