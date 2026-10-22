done
**Suggested title**
`Pattern comprehension may drop matched null projection elements instead of returning [null]`

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
- Client used for direct reproduction: local Python stage4 executor over Neo4j Bolt and FalkorDB RESP
- Differential comparison target: Neo4j Community `2026.02.2`

**Describe the bug**
FalkorDB may drop `null` projection elements from a pattern comprehension even when the pattern itself matched successfully.

In the minimized repro below, the row for `Alice` has a real `[:FRIEND_OF]` match, but the matched opposite endpoint has no `name` property. Neo4j therefore keeps the matched slot and returns:

```cypher
[null]
```

FalkorDB instead returns:

```cypher
[]
```

This suggests the problem is not general pattern-comprehension support, but specifically the handling of matched rows whose projected expression evaluates to `null`.

**To Reproduce**
1. Start the FalkorDB Docker container.
2. Run:

```cypher
CREATE (:Person {name:'Alice'})-[:FRIEND_OF]->(:Person);
CREATE (:Person {name:'Bob'})-[:WORKS_WITH]->(:Person {name:'Carol'});

MATCH (n:Person)
RETURN n.name AS nodeName,
       [(n)-[:FRIEND_OF]-(m) | m.name] AS friendNames
ORDER BY nodeName
LIMIT 10;
```

**Expected behavior**
Observed Neo4j result:

```text
Alice, [null]
Bob, []
Carol, []
null, [Alice]
```

FalkorDB should preserve the matched null-valued projection element for `Alice` and return the same rows.

**Actual behavior**
Observed FalkorDB result:

```text
Alice, []
Bob, []
Carol, []
null, [Alice]
```

The `Alice` row loses the matched `null` projection element and becomes `[]`.

**Control query 1**
When the projected expression is non-null, both engines behave normally:

```cypher
CREATE (:Person {name:'Alice'})-[:FRIEND_OF]->(:Person {name:'Bob'});

MATCH (n:Person {name:'Alice'})
RETURN [(n)-[:FRIEND_OF]-(m) | m.name] AS friendNames;
```

Observed result on both Neo4j and FalkorDB:

```text
["Bob"]
```

So FalkorDB is not failing to materialize pattern-comprehension rows in general.

**Control query 2**
If the nullable projection is stabilized with `coalesce`, both engines also agree:

```cypher
CREATE (:Person {name:'Alice'})-[:FRIEND_OF]->(:Person);

MATCH (n:Person {name:'Alice'})
RETURN [(n)-[:FRIEND_OF]-(m) | coalesce(m.name, '<missing>')] AS friendNames;
```

Observed result on both Neo4j and FalkorDB:

```text
["<missing>"]
```

This narrows the issue to projection elements that evaluate to `null` after a successful match, rather than unmatched rows or pattern comprehension syntax in general.

**Differential seed**
This family was first noticed in:

- `RedisGraph__RedisGraph__1658__q002`

**Related note**
This looks distinct from:

- [falkordb_pattern_comprehension_in_where_may_fail_with_filtered_alias_candidate.md](D:/sci/works/get%20bugs/graph-bug-fuzz-mvp/issue_reports/candidates/falkordb/falkordb_pattern_comprehension_in_where_may_fail_with_filtered_alias_candidate.md)

That family is about alias-resolution failure inside `WHERE`. The current family is a pure wrong-result issue in projected pattern-comprehension output.

**Verification Environment**
Plain FalkorDB Docker image is enough.

Preferred verification target:
- Docker image
- x86_64
- `falkordb/falkordb`

I can verify a fix using:
- plain FalkorDB Docker image on Windows host
- local Python stage4 executor over RESP
