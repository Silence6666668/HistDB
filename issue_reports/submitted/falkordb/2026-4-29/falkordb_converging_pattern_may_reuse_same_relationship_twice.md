done
**Suggested title**
`Converging pattern may reuse the same relationship twice and fabricate extra matches`

**FalkorDB version**
Observed on:

- FalkorDB `4.18.1`
- FalkorDB `4.18.2`
- FalkorDB `4.18.3`

Docker image used locally:
- `falkordb/falkordb:latest`
- `falkordb/falkordb:v4.18.2`
- `falkordb/falkordb:v4.18.3`

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
FalkorDB may reuse the same relationship for two different relationship slots inside a single converging pattern, which fabricates extra matches that should not exist.

In the minimized repro below, the pattern:

```cypher
(a:Person)-[:FRIEND]->(b:Person)<-[:FRIEND]-(c:Person)
```

should only match rows where the two `[:FRIEND]` edges are distinct.

Neo4j returns only the two expected permutations:

- `Alice, Bob, Charlie`
- `Charlie, Bob, Alice`

FalkorDB additionally returns:

- `Alice, Bob, Alice`
- `Charlie, Bob, Charlie`

which suggests FalkorDB is allowing one physical relationship to satisfy both relationship positions in the same pattern.

**To Reproduce**
1. Start the FalkorDB Docker container.
2. Run:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice'})-[:FRIEND]->(:Person {name:'Bob'})<-[:FRIEND]-(:Person {name:'Charlie'});

MATCH (a:Person)-[:FRIEND]->(b:Person)<-[:FRIEND]-(c:Person)
RETURN a.name AS a, b.name AS b, c.name AS c
ORDER BY a, c;
```

**Expected behavior**
Neo4j returns:

```text
Alice, Bob, Charlie
Charlie, Bob, Alice
```

FalkorDB should return the same two rows.

**Actual behavior**
FalkorDB returns:

```text
Alice, Bob, Alice
Alice, Bob, Charlie
Charlie, Bob, Alice
Charlie, Bob, Charlie
```

So FalkorDB fabricates two extra rows where the same endpoint appears on both sides of the converging pattern.

**Control query 1**
With only a single incoming relationship, the pattern should have no valid match at all, because one relationship cannot fill both slots.

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice'})-[:FRIEND]->(:Person {name:'Bob'});

MATCH (a:Person)-[:FRIEND]->(b:Person)<-[:FRIEND]-(c:Person)
RETURN a.name AS a, b.name AS b, c.name AS c
ORDER BY a, c;
```

Observed result:

- Neo4j: `0 rows`
- FalkorDB: `Alice, Bob, Alice`

This makes the issue much sharper: FalkorDB can report a match even when there is only one relationship available.

**Control query 2**
If the two relationship slots are bound explicitly and required to be different, FalkorDB behaves normally:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice'})-[:FRIEND]->(:Person {name:'Bob'})<-[:FRIEND]-(:Person {name:'Charlie'});

MATCH (a:Person)-[r1:FRIEND]->(b:Person)<-[r2:FRIEND]-(c:Person)
WHERE r1 <> r2
RETURN a.name AS a, b.name AS b, c.name AS c
ORDER BY a, c;
```

Observed result on both Neo4j and FalkorDB:

```text
Alice, Bob, Charlie
Charlie, Bob, Alice
```

This strongly suggests the bug is specifically tied to FalkorDB reusing the same relationship for both pattern positions.

**Why this matters**
This looks like a deeper root cause than a pure aggregation bug. It likely explains why some larger FalkorDB seeds over-count rows or return duplicated aggregates on converging patterns.

**Differential seeds**
This deeper family was noticed while rechecking larger wrong-result cases including:

- `FalkorDB__FalkorDB__699__q005`
- `FalkorDB__FalkorDB__309__q012`
- `RedisGraph__RedisGraph__748__q003`
- `RedisGraph__RedisGraph__2631__q005`
- `RedisGraph__RedisGraph__748__q009`

**Additional supporting reproducer**
The same root cause also appears in a shorter undirected-chain aggregation:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Person {name:'Alice', age:30}),
       (b:Person {name:'Bob', age:25}),
       (c:Person {name:'Charlie', age:35}),
       (a)-[:KNOWS]->(b),
       (b)-[:KNOWS]->(c);

MATCH (a:Person)-[:KNOWS]-(b:Person)-[:KNOWS]-(c:Person)
WHERE a.age > 25
WITH a.name AS person_name, COUNT(b) AS connection_count
WHERE connection_count > 0
RETURN person_name, connection_count
ORDER BY connection_count DESC, person_name;
```

Observed Neo4j result:

```text
Alice, 1
Charlie, 1
```

Observed FalkorDB result:

```text
Alice, 2
Charlie, 2
```

If the two relationship slots are bound explicitly and required to differ:

```cypher
MATCH (a:Person)-[r1:KNOWS]-(b:Person)-[r2:KNOWS]-(c:Person)
WHERE a.age > 25 AND r1 <> r2
WITH a.name AS person_name, COUNT(b) AS connection_count
WHERE connection_count > 0
RETURN person_name, connection_count
ORDER BY connection_count DESC, person_name;
```

then both Neo4j and FalkorDB return:

```text
Alice, 1
Charlie, 1
```

This makes the boundary clearer: FalkorDB is over-counting because the same physical relationship can be reused for both slots in the undirected converging pattern unless the query explicitly forbids it.

**Additional supporting reproducer 2**
The same root cause also appears when the converging pattern is first matched with concrete relationship variables and then re-used inside a later pattern comprehension:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Person {name:'Alice', age:30}),
       (b:Person {name:'Bob', age:25}),
       (c:Person {name:'Charlie', age:35}),
       (a)-[:KNOWS {since: 2020}]->(b),
       (c)-[:KNOWS {since: 2021}]->(b);

MATCH (a:Person)-[r1:KNOWS]->(b:Person)<-[r2:KNOWS]-(c:Person)
WHERE a.age > 25 AND c.age < 40
WITH a, r1, b, r2, c
MATCH (a)-[r1]->(b)<-[r2]-(c)
RETURN a.name AS person_name,
       [(a)-[r1]->(b) | r1.since] AS subquery_result,
       r2.since AS relationship_since;
```

Observed Neo4j result:

```text
0 rows
```

Observed FalkorDB result:

```text
Charlie, [2021], 2021
Alice,   [2020], 2020
```

So FalkorDB can fabricate converging matches even when the re-used relationship variables should make the pattern impossible. This is consistent with the same deeper bug: one physical relationship is allowed to satisfy both converging slots.

**Additional supporting reproducer 3**
The same root cause also appears in a simpler undirected count query without explicit relationship variables:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Person {name:'Alice', age:30}),
       (b:Person {name:'Bob', age:25}),
       (c:Person {name:'Charlie', age:35}),
       (a)-[:KNOWS]->(b),
       (b)-[:KNOWS]->(c);

MATCH (a:Person)-[:KNOWS]-(b:Person)-[:KNOWS]-(c:Person)
WHERE a.age > 25
WITH a.name AS person_name, COUNT(b) AS connection_count
WHERE connection_count > 0
RETURN person_name, connection_count
ORDER BY connection_count DESC;
```

Observed Neo4j result:

```text
Alice, 2
Charlie, 2
```

Observed FalkorDB result:

```text
Alice, 4
Charlie, 4
```

This again points to the same deeper issue: the shared middle relationship position can be satisfied multiple times by the same physical edge, which inflates the final count.

**Additional supporting reproducer 4 (current version)**
The same root cause still reproduces on FalkorDB `4.18.2` in a smaller undirected chain:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Person {name:'Alice'})-[:KNOWS]->(b:Person {name:'Bob'})-[:FRIEND]->(c:Person {name:'Charlie'});

MATCH (a:Person)-[]-(b:Person)-[]-(c:Person)
RETURN a.name AS a, b.name AS b, c.name AS c
ORDER BY a, b, c;
```

Observed Neo4j result:

```text
Alice, Bob, Charlie
Charlie, Bob, Alice
```

Observed FalkorDB `4.18.2` result:

```text
Alice, Bob, Alice
Alice, Bob, Charlie
Bob, Alice, Bob
Bob, Charlie, Bob
Charlie, Bob, Alice
Charlie, Bob, Charlie
```

So the same physical edge is still being reused to satisfy both undirected slots around the middle node. This is consistent with the same deeper family rather than a separate aggregation-only bug.

**Additional supporting reproducer 5 (current version)**
The same root cause also appears in a longer skeleton-derived pattern where Neo4j should only classify one valid row as `not found`:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Person {name: 'Alice'})-[:KNOWS]->(b:Person {name: 'Bob'})-[:KNOWS]->(c:Person {name: 'Charlie'})-[:KNOWS]->(d:Person {name: 'David'})<-[:KNOWS]-(e:Person {name: 'Eve'})-[:KNOWS]->(f:Person {name: 'Frank'});

MATCH p=()<--(n1:Person)--(n2:Person)--(n3:Person)<--()
RETURN DISTINCT CASE WHEN n1.name = 'Alice' THEN 'found' ELSE 'not found' END AS result
LIMIT 5;
```

Observed Neo4j result:

```text
not found
```

Observed FalkorDB `4.18.2` result:

```text
found
not found
```

If the raw rows are returned instead:

```cypher
MATCH p=()<--(n1:Person)--(n2:Person)--(n3:Person)<--()
RETURN DISTINCT n1.name AS n1, n2.name AS n2, n3.name AS n3
ORDER BY n1, n2, n3;
```

Neo4j returns only:

```text
Eve, David, Charlie
```

but FalkorDB additionally fabricates rows such as:

```text
Alice, Bob, Charlie
Bob, Alice, Bob
Charlie, Bob, Charlie
```

So this is still the same deeper family: undirected relationship slots can be satisfied by reusing the same physical edge, which creates spurious matches and then leaks into later projections such as `CASE`.

**Additional supporting reproducer 6 (current version)**
The same root cause also appears in a smaller converging pattern where Neo4j should find no valid rows at all:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (:Node {name:'A', active:true})-[:R]->(:Node {name:'B', active:false}),
       (:Node {name:'C', active:true})-[:R]->(:Node {name:'D', active:false});

MATCH (n0)<-[r0]-(n1)-[r1]->(n2)
RETURN id(r0), id(r1), n0, n2, head([]) AS empty_head, [] AS empty_list
ORDER BY null;
```

Observed Neo4j result:

```text
0 rows
```

Observed FalkorDB `4.18.2` result:

```text
id(r0)=0, id(r1)=0, n0=B, n2=B
id(r0)=1, id(r1)=1, n0=D, n2=D
```

So FalkorDB is again reusing the same physical relationship for both adjacent slots around the middle node. That creates fabricated rows in which `r0` and `r1` are the same edge and `n0` equals `n2`, even though Neo4j finds no legal match.

**Additional supporting reproducer 7 (current version)**
The same root cause also appears in a skeleton-derived directed-then-undirected chain, where FalkorDB overcounts by reusing the same physical edge around the middle node:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Node {name:'A'}), (b:Node {name:'B'}), (c:Node {name:'C'}), (d:Node {name:'D'});
MATCH (a:Node {name:'A'}), (b:Node {name:'B'}), (c:Node {name:'C'}), (d:Node {name:'D'})
CREATE (a)-[:REL]->(b), (b)-[:REL]->(c), (c)-[:REL]->(d);

MATCH ()-[*1..3]->(:Node)--()
RETURN count(*) AS cnt, 'constant' AS const;
```

Observed results:

- Neo4j:

```text
3, constant
```

- FalkorDB `4.18.2`:

```text
9, constant
```

If the raw rows are expanded instead:

```cypher
MATCH ()-[*1..3]->(m:Node)--(x)
RETURN m.name AS mid, x.name AS x
ORDER BY mid, x;
```

Neo4j returns:

```text
B, A
B, C
C, D
```

but FalkorDB additionally fabricates repeated rows such as:

```text
C, B
C, B
D, C
D, C
```

So this is still the same deeper family: once an undirected slot is adjacent to a previously matched relationship chain, FalkorDB can reuse physical edges to satisfy multiple slots and inflate the final count.

**Additional supporting reproducer 8 (current version)**
The same root cause also appears across two pattern parts in the same `MATCH`, where Neo4j preserves relationship uniqueness across the whole clause but FalkorDB reuses the same physical edge in both `p` and `q`:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Person {name: 'Alice', age: 30}),
       (b:Person {name: 'Bob', age: 25}),
       (c:Person {name: 'Charlie', age: 35}),
       (a)-[:KNOWS]->(b),
       (b)-[:KNOWS]->(c),
       (c)-[:KNOWS]->(a);

MATCH p = (x1:Person {name: 'Alice'})<-[*]-(x2:Person),
      q = (x1)<-[r:KNOWS]-(x2)
RETURN p, q, [n IN nodes(p) | n.age] + [n IN nodes(q) | n.name] AS concatenated_list;
```

Observed Neo4j result:

```text
0 rows
```

Observed FalkorDB `4.18.2` result:

```text
p = Alice <-[:KNOWS]- Charlie
q = Alice <-[:KNOWS]- Charlie
concatenated_list = [30, 35, 'Alice', 'Charlie']
```

The only apparent way to obtain this FalkorDB row is to let the same physical `Charlie-[:KNOWS]->Alice` relationship satisfy both pattern parts at once. Neo4j correctly rejects that reuse and returns no row.

**Additional supporting reproducer 9 (current version)**
The same root cause also appears when one pattern part binds a single relationship and a second pattern part binds a variable-length incoming chain into the same middle node:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Person:Employee {name: 'Alice', age: 30}),
       (b:Person:Employee {name: 'Bob', age: 25}),
       (c:Person:Employee {name: 'Charlie', age: 35}),
       (a)-[:WORKS_WITH]->(b),
       (b)-[:WORKS_WITH]->(c),
       (c)-[:WORKS_WITH]->(a);

MATCH ()-[r]->()<-[s*1..2]-(:Person:Employee)
RETURN r, s;
```

Observed Neo4j result:

```text
0 rows
```

Observed FalkorDB `4.18.2` result:

```text
r = Alice-[:WORKS_WITH]->Bob,     s = Alice-[:WORKS_WITH]->Bob
r = Bob-[:WORKS_WITH]->Charlie,   s = Alice-[:WORKS_WITH]->Bob-[:WORKS_WITH]->Charlie
r = Bob-[:WORKS_WITH]->Charlie,   s = Bob-[:WORKS_WITH]->Charlie
r = Charlie-[:WORKS_WITH]->Alice, s = Bob-[:WORKS_WITH]->Charlie-[:WORKS_WITH]->Alice
r = Charlie-[:WORKS_WITH]->Alice, s = Charlie-[:WORKS_WITH]->Alice
r = Alice-[:WORKS_WITH]->Bob,     s = Charlie-[:WORKS_WITH]->Alice-[:WORKS_WITH]->Bob
```

The first and third rows are especially telling: FalkorDB is willing to let the exact same physical relationship satisfy both `r` and the whole `s` binding when `s` has length `1`. Neo4j rejects those reuses entirely and returns no row.

**Additional supporting reproducer 10 (current version)**
The same root cause also appears in a two-part directed pattern where Neo4j enforces relationship uniqueness across the whole `MATCH`, but FalkorDB reuses the same physical edge in both parts:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Person {name:'Alice'}),
       (b:Person {name:'Bob'}),
       (c:Person {name:'Charlie'}),
       (d:Person {name:'David'}),
       (a)-[:KNOWS]->(b),
       (b)-[:KNOWS]->(c),
       (b)-[:KNOWS]->(d),
       (d)-[:KNOWS]->(b);

MATCH (a:Person {name: 'Alice'})-[:KNOWS]->(b:Person)-[:KNOWS]->(c:Person),
      (b)-[:KNOWS]->(d:Person)-[:KNOWS]->(b)
RETURN a.name AS a, b.name AS b, c.name AS c, d.name AS d;
```

Observed results:

- Neo4j:

```text
Alice, Bob, Charlie, David
```

- FalkorDB `v4.18.2`:

```text
Alice, Bob, Charlie, David
Alice, Bob, David, David
```

The extra FalkorDB row can only appear if the same physical `Bob-[:KNOWS]->David` edge is reused for both the `b-[:KNOWS]->c` slot and the `b-[:KNOWS]->d` slot inside the same `MATCH`.

**Control for supporting reproducer 10**
If the two pattern parts are separated by `WITH`, both engines agree on the two-row result:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Person {name:'Alice'}),
       (b:Person {name:'Bob'}),
       (c:Person {name:'Charlie'}),
       (d:Person {name:'David'}),
       (a)-[:KNOWS]->(b),
       (b)-[:KNOWS]->(c),
       (b)-[:KNOWS]->(d),
       (d)-[:KNOWS]->(b);

MATCH (a:Person {name: 'Alice'})-[:KNOWS]->(b:Person)-[:KNOWS]->(c:Person)
WITH a, b, c
MATCH (b)-[:KNOWS]->(d:Person)-[:KNOWS]->(b)
RETURN a.name AS a, b.name AS b, c.name AS c, d.name AS d;
```

Observed result on both Neo4j and FalkorDB:

```text
Alice, Bob, Charlie, David
Alice, Bob, David, David
```

So the mismatch is specifically about relationship reuse inside a single `MATCH` clause, which is exactly the boundary of this family.

**Additional supporting reproducer 11 (current version)**
The same root cause also appears in a smaller undirected chain with aggregation. Neo4j finds only one legal row, but FalkorDB fabricates a second row by reusing the same physical edge on both sides of the middle node:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Person {name:'Alice', age:30, city:'NYC'}),
       (b:Person {name:'Bob', age:25}),
       (c:Person {name:'Charlie', age:35}),
       (a)-[:KNOWS]->(b),
       (b)-[:KNOWS]->(c);

MATCH (s1:Person {name: 'Alice'})--(n)--(s2:Person)
RETURN s1.age AS age1,
       s2.age AS age2,
       s1.city AS city1,
       collect(labels(n)) AS labels,
       collect(n.name) AS names,
       sum(n.age) AS total_age;
```

Observed results:

- Neo4j:

```text
30, 35, NYC, [[Person]], [Bob], 25
```

- FalkorDB `v4.18.2`:

```text
30, 30, NYC, [[Person]], [Bob], 25
30, 35, NYC, [[Person]], [Bob], 25
```

If the raw rows are expanded instead:

```cypher
MATCH (s1:Person {name: 'Alice'})--(n)--(s2:Person)
RETURN s1.name AS s1, n.name AS n, s2.name AS s2
ORDER BY s1, n, s2;
```

Neo4j returns:

```text
Alice, Bob, Charlie
```

but FalkorDB `v4.18.2` returns:

```text
Alice, Bob, Alice
Alice, Bob, Charlie
```

So FalkorDB is again willing to let the same physical `Alice-[:KNOWS]->Bob` relationship satisfy both adjacent undirected slots around `Bob`, which creates a fabricated `Alice, Bob, Alice` row and then leaks into the grouped aggregation.

**Additional supporting reproducer 12 (current version)**
The same root cause can be reduced even further to a graph with only one physical relationship:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (b:Person {name:'Bob'}),
       (c:Person {name:'Charlie'}),
       (b)-[:FRIEND]->(c);

MATCH (n1)<-[r:FRIEND]-(n2)-[:FRIEND]->(n3:Person)
RETURN n1.name AS n1, n2.name AS n2, n3.name AS n3;
```

Observed results:

- Neo4j:

```text
0 rows
```

- FalkorDB `v4.18.2`:

```text
Charlie, Bob, Charlie
```

The only way to obtain this FalkorDB row is to let the single physical `Bob-[:FRIEND]->Charlie` relationship satisfy both adjacent slots at once: once as `n2-[:FRIEND]->n3` and again as `n1<-[:FRIEND]-n2`. Neo4j correctly rejects that reuse and returns no row.

**Additional supporting reproducer 13 (current version)**
The same root cause also survives through later aggregation and even after an unrelated `DELETE` step:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Person {name:'Alice'}),
       (b:Person {name:'Bob'}),
       (c:Person {name:'Charlie'}),
       (a)-[:KNOWS]->(b),
       (b)-[:KNOWS]->(c);

MATCH p = (a)--(b)--(c)
OPTIONAL MATCH (a)-[r]->(d)
DELETE r
WITH a, b, c, count(d) AS cnt
OPTIONAL MATCH (e:Person)
RETURN a.name AS a, b.name AS b, c.name AS c, cnt, count(e) AS total
ORDER BY a, b, c;
```

Observed results:

- Neo4j:

```text
Alice,   Bob, Charlie, 1, 3
Charlie, Bob, Alice,   0, 3
```

- FalkorDB `v4.18.2`:

```text
Alice,   Bob, Alice,   1, 3
Alice,   Bob, Charlie, 1, 3
Bob,     Alice, Bob,   1, 3
Bob,     Charlie, Bob, 1, 3
Charlie, Bob, Alice,   0, 3
Charlie, Bob, Charlie, 0, 3
```

The `DELETE r` is not the root cause. If the `DELETE` is removed and the query is rerun with:

```cypher
MATCH p = (a)--(b)--(c)
OPTIONAL MATCH (a)-[r]->(d)
WITH a, b, c, count(d) AS cnt
OPTIONAL MATCH (e:Person)
RETURN a.name AS a, b.name AS b, c.name AS c, cnt, count(e) AS total
ORDER BY a, b, c;
```

Neo4j and FalkorDB still differ in exactly the same way. So this is still the same deeper family: FalkorDB fabricates extra `(a)--(b)--(c)` rows by reusing the same physical edge on both sides of the middle node, and the bad rows then leak into later aggregation.

**Additional supporting reproducer 14 (current version)**
The same root cause also appears in a smaller outgoing-chain pattern where Neo4j finds no legal match at all, but FalkorDB reuses each physical relationship as both adjacent slots around the middle node:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Person {name:'Alice'}),
       (b:Person {name:'Bob'}),
       (c:Person {name:'Charlie'}),
       (a)-[:KNOWS]->(b),
       (b)-[:KNOWS]->(c);

MATCH ()<-[a]-()-[b]->()
RETURN count(*) AS cnt;
```

Observed results:

- Neo4j:

```text
0
```

- FalkorDB `v4.18.2`:

```text
2
```

If the raw rows are expanded instead:

```cypher
MATCH ()<-[a]-()-[b]->()
RETURN startNode(a).name AS a_src,
       endNode(a).name AS a_dst,
       startNode(b).name AS b_src,
       endNode(b).name AS b_dst
ORDER BY a_src, a_dst, b_src, b_dst;
```

Neo4j returns:

```text
0 rows
```

but FalkorDB `v4.18.2` returns:

```text
Alice, Bob, Alice, Bob
Bob,   Charlie, Bob, Charlie
```

So each physical relationship is being reused to satisfy both adjacent slots by itself. This same family also explains the skeleton-derived query:

```cypher
MATCH ()<-[a]-()-[b]->()
WITH a, b, id(a) AS idA, id(b) AS idB
MATCH (x:Person)
WHERE x.name IN ['Alice', 'Bob']
WITH a, b, idA, idB, collect(x.name) AS names
ORDER BY idA
SKIP 1
LIMIT 2
RETURN a, b, names;
```

Observed results:

- Neo4j:

```text
0 rows
```

- FalkorDB `v4.18.2`:

```text
Alice-[:KNOWS]->Bob, Alice-[:KNOWS]->Bob, ['Alice', 'Bob']
```

So even after `WITH`, ordering, `SKIP`, and `LIMIT`, the same fabricated same-edge-reuse row survives and leaks into later projection.

**Additional supporting reproducer 15 (current version)**
The same root cause also appears in a smaller directed converging pattern where Neo4j should find no rows, but FalkorDB reuses each directed edge by itself to satisfy both adjacent slots:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Person {name: 'Alice'}),
       (b:Person {name: 'Bob'}),
       (c:Person {name: 'Charlie'}),
       (a)-[:KNOWS]->(b),
       (b)-[:KNOWS]->(c);

MATCH (a:Person)-[:KNOWS]->(b)<-[:KNOWS]-(c)
RETURN a.name, b.name, c.name
ORDER BY a.name, b.name, c.name;
```

Observed results:

- Neo4j:

```text
0 rows
```

- FalkorDB `v4.18.3`:

```text
Alice, Bob, Alice
Bob,   Charlie, Bob
```

So this is still the same deeper family: FalkorDB is willing to let one physical directed relationship satisfy both adjacent converging slots, which fabricates rows that should be impossible.

**Additional supporting reproducer 16 (current version)**
The same root cause also appears across multiple comma-separated pattern parts in one `MATCH`, where Neo4j preserves relationship uniqueness across the whole clause but FalkorDB reuses one physical outgoing edge twice:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Person {name:'Alice'}),
       (b:Person {name:'Bob'}),
       (c:Person {name:'Charlie'}),
       (d:Person {name:'Diana'}),
       (a)-[:KNOWS]->(b),
       (a)-[:KNOWS]->(c),
       (b)-[:KNOWS]->(d),
       (c)-[:KNOWS]->(d);

MATCH (a:Person {name:'Alice'})-[:KNOWS]->(b:Person),
      (a)-[:KNOWS]->(c:Person),
      (b)-[:KNOWS]->(d:Person),
      (c)-[:KNOWS]->(d:Person)
RETURN b.name, c.name, d.name
ORDER BY b.name, c.name, d.name;
```

Observed results:

- Neo4j:

```text
Bob,     Charlie, Diana
Charlie, Bob,     Diana
```

- FalkorDB `v4.18.3`:

```text
Bob,     Bob,     Diana
Bob,     Charlie, Diana
Charlie, Bob,     Diana
Charlie, Charlie, Diana
```

The extra FalkorDB rows can only appear if `Alice-[:KNOWS]->Bob` is reused for both outgoing slots in the first row, and `Alice-[:KNOWS]->Charlie` is reused for both outgoing slots in the last row. So this is still the same deeper family: relationship uniqueness is broken across multiple pattern parts in one `MATCH`, not just inside one textual chain.

**Additional supporting reproducer 17 (current version)**
The same deeper root cause also appears when one pattern part uses a variable-length path and another uses a direct relationship from the same start node. Neo4j preserves relationship uniqueness between the path and the direct edge, but FalkorDB can still reuse a relationship that is already inside the path:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Person {name: 'Alice'}),
       (b:Person {name: 'Bob'}),
       (c:Person {name: 'Charlie'}),
       (a)-[:KNOWS]->(b),
       (b)-[:KNOWS]->(c),
       (a)-[:KNOWS]->(c);

MATCH p=(a:Person)-[:KNOWS*1..2]->(b:Person),
      (a)-[r:KNOWS]->(c:Person)
RETURN a.name AS a, b.name AS b, c.name AS c
ORDER BY a, b, c;
```

Observed results:

- Neo4j:

```text
Alice, Bob,     Charlie
Alice, Charlie, Bob
Alice, Charlie, Charlie
```

- FalkorDB `v4.18.3`:

```text
Alice, Bob,     Bob
Alice, Bob,     Charlie
Alice, Charlie, Bob
Alice, Charlie, Bob
Alice, Charlie, Charlie
Alice, Charlie, Charlie
Bob,   Charlie, Charlie
```

The extra FalkorDB rows imply that relationships already used inside `p` are being reused again for the direct edge `r`. For example, `Alice, Bob, Bob` can only appear if `Alice-[:KNOWS]->Bob` is used both as the first edge of the variable-length path and again as `r`, while `Bob, Charlie, Charlie` can only appear if `Bob-[:KNOWS]->Charlie` is reused the same way.

If explicit path-edge uniqueness is enforced:

```cypher
MATCH p=(a:Person)-[:KNOWS*1..2]->(b:Person),
      (a)-[r:KNOWS]->(c:Person)
WHERE none(rel IN relationships(p) WHERE rel = r)
RETURN a.name AS a, b.name AS b, c.name AS c
ORDER BY a, b, c;
```

then both Neo4j and FalkorDB `v4.18.3` return:

```text
Alice, Bob,     Charlie
Alice, Charlie, Bob
Alice, Charlie, Charlie
```

So this is still the same deeper family: FalkorDB is fabricating extra rows because relationship uniqueness is not preserved between a variable-length path and a direct relationship pattern in the same `MATCH`.

**Additional supporting reproducer 18 (current version)**
The same deeper root cause also appears across comma-separated directed pattern parts, where one outgoing edge from `a` is already used as the first hop of a longer chain but FalkorDB can still reuse that same physical edge again for another adjacent slot:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Person {name: 'Alice', age: 30}),
       (b:Person {name: 'Bob', age: 25}),
       (c:Person {name: 'Charlie', age: 35}),
       (d:Person {name: 'Diana', age: 28}),
       (a)-[:KNOWS]->(b),
       (b)-[:KNOWS]->(c),
       (a)-[:KNOWS]->(d);

MATCH (a:Person)-[:KNOWS]->(b:Person)-[:KNOWS]->(c:Person),
      (a)-[:KNOWS]->(d:Person)
WHERE a.age > b.age AND b.age < c.age
RETURN b.name AS b, c.name AS c, d.name AS d
ORDER BY b, c, d;
```

Observed results:

- Neo4j:

```text
Bob, Charlie, Diana
```

- FalkorDB `v4.18.3`:

```text
Bob, Charlie, Bob
Bob, Charlie, Diana
```

The extra FalkorDB row `Bob, Charlie, Bob` can only appear if the physical relationship `Alice-[:KNOWS]->Bob` is reused both as the first hop of the chain `a->b->c` and again as the separate outgoing edge `a->d`.

If the two outgoing slots are bound explicitly and required to differ:

```cypher
MATCH (a:Person)-[r1:KNOWS]->(b:Person)-[r2:KNOWS]->(c:Person),
      (a)-[r3:KNOWS]->(d:Person)
WHERE a.age > b.age AND b.age < c.age AND r1 <> r3
RETURN b.name AS b, c.name AS c, d.name AS d
ORDER BY b, c, d;
```

then both Neo4j and FalkorDB `v4.18.3` return:

```text
Bob, Charlie, Diana
```

So this is still the same deeper family: a single physical directed edge can be reused across adjacent pattern slots in one `MATCH`, even when one of those slots is already part of a longer chain.

**Additional supporting reproducer 19 (current version)**
The same deeper root cause also appears when one adjacent slot is a self-loop and the other is an ordinary outgoing relationship from the same node. Neo4j preserves relationship uniqueness, but FalkorDB can still reuse the self-loop for both slots:

```cypher
MATCH (n) DETACH DELETE n;
CREATE (a:Person {name: 'Alice'}),
       (b:Person {name: 'Bob'}),
       (c:Person {name: 'Charlie'});

MATCH (a:Person {name: 'Alice'}), (b:Person {name: 'Bob'})
CREATE (a)-[:KNOWS]->(b);

MATCH (b:Person {name: 'Bob'}), (c:Person {name: 'Charlie'})
CREATE (b)-[:KNOWS]->(c);

MATCH (a:Person {name: 'Alice'})
CREATE (a)-[:KNOWS]->(a);

MATCH (p:Person)-[:KNOWS]->(friend:Person),
      (p)-[:KNOWS]->(self:Person)
WHERE p.name = 'Alice' AND p = self
RETURN friend.name AS friend, self.name AS self
ORDER BY friend, self;
```

Observed results:

- Neo4j:

```text
Bob, Alice
```

- FalkorDB `v4.18.3`:

```text
Alice, Alice
Bob,   Alice
```

The extra FalkorDB row `Alice, Alice` can only appear if the single self-loop `Alice-[:KNOWS]->Alice` is reused for both adjacent relationship slots.

If the two slots are bound explicitly and required to differ:

```cypher
MATCH (p:Person)-[r1:KNOWS]->(friend:Person),
      (p)-[r2:KNOWS]->(self:Person)
WHERE p.name = 'Alice' AND p = self AND r1 <> r2
RETURN friend.name AS friend, self.name AS self
ORDER BY friend, self;
```

then both Neo4j and FalkorDB `v4.18.3` return:

```text
Bob, Alice
```

So this is still the same deeper family: a single physical relationship can be reused across adjacent slots, here with a self-loop occupying both positions.

**Additional current-version note**
The same fabricated converging rows can also survive later row-multiplying clauses unchanged. For example, the skeleton-derived case:

```cypher
MATCH (a:Person)-[:KNOWS]->(b:Person)<-[:KNOWS]-(c:Person)
WITH a, b, c
UNWIND [a, b, c] AS node
FOREACH (x IN CASE WHEN size([]) > 0 THEN [] ELSE [] END | SET x.marked = true)
RETURN a.name, b.name, c.name;
```

returns `0 rows` on Neo4j, but FalkorDB `v4.18.3` returns each impossible row three times, such as `Alice, Bob, Alice` and `David, Eve, David`. That later `UNWIND`/no-op `FOREACH` shape does not introduce a new root cause; it simply multiplies the same illegal same-edge-reuse rows after FalkorDB has already fabricated them.

**Additional current-version note 2**
The same deeper root cause also appears on a converging city pattern. If only `Alice` lives in `New York`, then:

```cypher
MATCH (p:Person {name: 'Alice'})-[:LIVES_IN]->(city:City)<-[:LIVES_IN]-(other:Person)
RETURN p.name, other.name, city.name;
```

should return `0 rows`. Neo4j does. But FalkorDB `v4.18.3` can still fabricate:

```text
Alice, Alice, New York
```

The only apparent way to obtain that FalkorDB row is to reuse the single physical `Alice-[:LIVES_IN]->New York` relationship for both incoming slots around `city`.

**Verification Environment**
Plain FalkorDB Docker image is enough.

Preferred verification target:
- Docker image
- x86_64
- `falkordb/falkordb`

I can verify a fix using:
- plain FalkorDB Docker image on Windows host
- direct `redis-cli GRAPH.QUERY`

**Stage3 provenance**
This report predates explicit file-level Stage3 provenance tracking.

Current-version supporting cases from later differential runs:
- Stage3 run: `historical_bug_queries_parallel_llm_v1_stage3x10_skeleton_20260429b`
- Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_falkordb_4_18_2_skeleton_20260501`
- Supporting seeds:
  - `FalkorDB__FalkorDB__321__q003` / `case_aa848623691c`
  - `FalkorDB__FalkorDB__403__q005` / `case_5dada957aa19`
  - `FalkorDB__FalkorDB__514__q001` / `case_7aff3e4892f2`
  - `FalkorDB__FalkorDB__514__q002` / `case_ff0e08a21d51`
  - `RedisGraph__RedisGraph__3058__q001` / `case_075abeca8224`
  - `RedisGraph__RedisGraph__2224__q002` / `case_263110f7cdca`
  - `RedisGraph__RedisGraph__2836__q005` / `case_0352eb4c2bc3`
  - `RedisGraph__RedisGraph__2877__q001` / `case_15e5d1a6daa6`
  - `RedisGraph__RedisGraph__446__q001` / `case_f0d267cf2666`
  - `RedisGraph__RedisGraph__1497__q001` / `case_415c61475c19`
  - `FalkorDB__FalkorDB__333__q002` / `case_2f92fd5fe049`
  - `FalkorDB__FalkorDB__1469__q004` / `case_9a42cf3205a0`

Later current-version supporting case:
- Stage3 run: `historical_bug_queries_parallel_llm_v1_feature_only_random_20260506`
- Differential run: `historical_bug_queries_parallel_llm_v1_stage45_neo4j_falkordb_4_18_3_feature_only_random_20260507`
- Supporting seed:
  - `FalkorDB__FalkorDB__396__q002` / `case_f2a03962fa36`
  - `RedisGraph__RedisGraph__867__q002` / `case_e248b7b89809`
  - `ArcadeData__arcadedb__3114__q003` / `case_e9bdd3814b5a`
  - `RedisGraph__RedisGraph__2553__q005` / `case_632e3d24ea40`
  - `RedisGraph__RedisGraph__1619__q003` / `case_1bfba45ff9ad`
  - `RedisGraph__RedisGraph__3114__q002` / `case_eda2142bc911`
  - `neo4j__13017__q001` / `case_5b62f2f3f0cc`
