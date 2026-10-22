**Suggested title**
`toInteger(large positive float) may wrap to INT64_MIN instead of preserving sign`

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
Memgraph may convert a large **positive** floating-point value to the most negative 64-bit integer when `toInteger(...)` is applied near or above the signed 64-bit upper bound.

In the minimized repro below, Neo4j returns the maximum signed 64-bit integer:

```text
9223372036854775807
```

Memgraph instead returns:

```text
-9223372036854775808
```

This looks like an overflow wraparound, because the sign flips even though the input value is positive.

**To Reproduce**
1. Start the Memgraph Docker container.
2. Run:

```cypher
RETURN toInteger(9223372036854775808.0) AS x;
```

**Expected behavior**
Observed Neo4j result:

```text
x = 9223372036854775807
```

At minimum, the result should not become negative for a positive input.

**Actual behavior**
Observed Memgraph result:

```text
x = -9223372036854775808
```

So Memgraph wraps the positive input to `INT64_MIN`.

**Control query 1**
At the negative boundary, both engines agree:

```cypher
RETURN toInteger(-9223372036854775808.0) AS x;
```

Observed result on Neo4j and Memgraph:

```text
x = -9223372036854775808
```

So the issue is not simply “large magnitude floats are unsupported”.

**Control query 2**
The same positive-wrap behavior also appears for nearby large positive values:

```cypher
RETURN toInteger(9223372036854775807.0) AS x;
RETURN toInteger(9223372036854775806.0) AS x;
```

Observed Neo4j result in both cases:

```text
x = 9223372036854775807
```

Observed Memgraph result in both cases:

```text
x = -9223372036854775808
```

This suggests the issue is not limited to a single exact threshold literal.

**Related signal**
The larger differential query that first exposed this area returned a different result for:

```cypher
RETURN round(12345678901234567890.0) AS big_round;
```

Observed results:

- Neo4j: `9.223372036854776e+18`
- Memgraph: `1.2345678901234567e+19`

That `round(...)` difference is weaker on its own, but it points in the same direction: very large positive floating-point values near or beyond the 64-bit integer boundary are handled differently.

**Differential seed**
This family was surfaced while minimizing:

- `FalkorDB__FalkorDB__443__q001`
- `case_b22636878931`

**Verification environment**
Verified directly against:

- Neo4j Docker container: `neo4j:2026.04.0`
- Memgraph Docker container: `memgraph/memgraph:3.9.0`
- Memgraph Docker container: `memgraph/memgraph:latest` (resolved locally to `3.10.1`)
- local differential-testing harness
- direct fresh reruns over Bolt
