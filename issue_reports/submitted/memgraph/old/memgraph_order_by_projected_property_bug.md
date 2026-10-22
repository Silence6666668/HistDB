**Memgraph version**
Memgraph `3.8.1`

Docker image used locally:
- `memgraph/memgraph:latest`

Explicit reproducible image tag:
- `memgraph/memgraph:3.8.1-relwithdebinfo`

**Environment**
- Host OS: Windows
- Architecture: x86_64
- Deployment: Docker
- Connection method: Bolt
- Client used for direct reproduction: Python `neo4j` Bolt driver, `cypher-shell`, and `mgconsole`
- Differential comparison target: Neo4j Community `2026.02.2`

**Describe the bug**
A short query that projects `employee.name` in `RETURN` and then orders by `employee.name` fails on Memgraph with:

`Only nodes, edges, maps, temporal types and graphs have properties to be looked up.`

The same query succeeds on Neo4j.

This looks like Memgraph is incorrectly handling `ORDER BY employee.name` after `employee.name` has already been projected in the `RETURN` clause.

**To Reproduce**
1. Start the Memgraph Docker container.
2. Run the following query:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice'})-[:WORKS_FOR]->(:Person {name:'Bob'}), (:Person {name:'Charlie'});
MATCH (a:Person {name:'Alice'}), (c:Person {name:'Charlie'}) CREATE (a)-[:KNOWS]->(c);

MATCH (employee:Person)-[:WORKS_FOR]->(p)
OPTIONAL MATCH (employee)-[:KNOWS]->(friend)
RETURN employee.name AS employee, p.name AS boss, friend.name AS optionalFriend
ORDER BY employee.name;
```

Local repro file:
- `repros/memgraph_order_by_projected_property_bug_min.cypher`

**Expected behavior**
The query should succeed and return:

```text
employee = Alice, boss = Bob, optionalFriend = Charlie
```

Neo4j returns this result.

**Actual behavior**
Memgraph fails with:

```text
Only nodes, edges, maps, temporal types and graphs have properties to be looked up.
```

**Logs**
Observed Memgraph error:

```text
Memgraph.ClientError.MemgraphError.MemgraphError | Only nodes, edges, maps, temporal types and graphs have properties to be looked up.
```

**Additional context**
I checked several controls while minimizing this issue:

1. Without `ORDER BY employee.name`, Memgraph succeeds.
2. With `ORDER BY boss`, Memgraph succeeds.
3. With `ORDER BY employee`, Memgraph succeeds.
4. Only `ORDER BY employee.name` triggers the error.

That suggests the issue is not in the `MATCH` or `OPTIONAL MATCH` structure itself, but specifically in handling the projected property expression inside `ORDER BY`.

**Verification Environment**
Plain Memgraph Docker image is enough.

Preferred verification target:
- Docker image
- x86_64
- `memgraph/memgraph`

I can verify a fix using:
- plain Memgraph Docker image on Windows host
- Bolt connection using `mgconsole` and Python Bolt driver
