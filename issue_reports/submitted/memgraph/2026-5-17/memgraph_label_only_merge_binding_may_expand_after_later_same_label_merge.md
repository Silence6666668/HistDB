**Suggested title**
`Label-only MERGE binding may expand after a later same-label MERGE unless separated by WITH`

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
Memgraph may incorrectly expand the row binding produced by a label-only `MERGE` when a later `MERGE` in the same query creates another node with the same label.

In the minimized repro below, `MERGE (e:Person)` is evaluated when only `Alice`, `Bob`, and `Charlie` exist, so Neo4j returns exactly those three bound `e` rows.

Memgraph instead also returns a fourth row where `e.name = 'Eve'`, even though `Eve` is only introduced by the *later* `MERGE`.

This does not look like a general issue with label-only `MERGE`, because both engines agree if the later node creation is removed, if the later node uses a different label, or if a `WITH e` barrier is inserted before the later `MERGE`.

**To Reproduce**
1. Start the Memgraph Docker container.
2. Run:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice'}), (:Person {name:'Bob'}), (:Person {name:'Charlie'})
MERGE (e:Person)
MERGE (:Person {name:'Eve'})
RETURN e.name AS eName
ORDER BY eName;
```

**Expected behavior**
Observed Neo4j result:

```text
Alice
Bob
Charlie
```

Memgraph should return the same three rows, because those are the `Person` nodes present when `MERGE (e:Person)` is evaluated.

**Actual behavior**
Observed Memgraph result:

```text
Alice
Bob
Charlie
Eve
```

So the later same-label `MERGE` appears to retroactively expand the earlier binding of `e`.

**Control query 1**
If the later same-label `MERGE` is removed, both engines behave normally:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice'}), (:Person {name:'Bob'}), (:Person {name:'Charlie'})
MERGE (e:Person)
RETURN e.name AS eName
ORDER BY eName;
```

Observed result on Neo4j and Memgraph:

```text
Alice
Bob
Charlie
```

So label-only `MERGE` itself is not broken in complete generality.

**Control query 2**
If a `WITH e` barrier is inserted before the later `MERGE`, both engines also align:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice'}), (:Person {name:'Bob'}), (:Person {name:'Charlie'})
MERGE (e:Person)
WITH e
MERGE (:Person {name:'Eve'})
RETURN e.name AS eName
ORDER BY eName;
```

Observed result on Neo4j and Memgraph:

```text
Alice
Bob
Charlie
```

This makes the boundary sharper: the problem is tied to the later same-label `MERGE` interacting with the earlier binding in the same pipeline.

**Control query 3**
If the later creation uses a different label, both engines also behave normally:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice'}), (:Person {name:'Bob'}), (:Person {name:'Charlie'})
MERGE (e:Person)
CREATE (:City {name:'Paris'})
RETURN e.name AS eName
ORDER BY eName;
```

Observed result on Neo4j and Memgraph:

```text
Alice
Bob
Charlie
```

So this does not look like a generic “later write changes earlier rows” issue. The interaction appears specific to introducing another node with the same label.

**Related larger differential query**
The same family also appeared in a larger write query:

```cypher
CREATE (a:Person {name: 'Alice'}), (b:Person {name: 'Bob'}), (c:Person {name: 'Charlie'})
CREATE (a)-[:KNOWS]->(b)
CREATE (b)-[:KNOWS]->(c)
CREATE (c)-[:KNOWS]->(a)
MERGE (d:Person {name: 'David'})
MERGE (e:Person)
MERGE (f:Person {name: 'Eve'}) ON CREATE SET f.age = 30
MERGE (a)-[:FRIEND {since: 2020}]->(b)
MERGE (b)-[:FRIEND {since: 2021}]->(c)
MERGE (c)-[:FRIEND {since: 2022}]->(a)
MERGE (d)-[:COLLEAGUE {id: 1}]->(e)
MERGE (e)-[:COLLEAGUE {id: 2}]->(f)
RETURN a.name, b.name, c.name, d.name, e.name, f.name;
```

Observed result summary:

- Neo4j returned `4` rows, with `e.name` in `{Alice, Bob, Charlie, David}`
- Memgraph returned `5` rows, with `e.name` in `{Alice, Bob, Charlie, David, Eve}`

That larger differential case reduces cleanly to the smaller reproducer above.

**Differential seed**
This family was surfaced while minimizing:

- `FalkorDB__FalkorDB__243__q003`
- `case_e82461f7927b`

**Verification environment**
Verified directly against:

- Neo4j Docker container: `neo4j:2026.04.0`
- Memgraph Docker container: `memgraph/memgraph:3.9.0`
- Memgraph Docker container: `memgraph/memgraph:latest` (resolved locally to `3.10.1`)
- local differential-testing harness
- direct fresh reruns over Bolt
