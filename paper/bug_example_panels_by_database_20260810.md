# Paper-ready Bug Examples by Database

本文档从项目已归档的 `submitted` 报告中，为 Neo4j、Memgraph、FalkorDB、ArcadeDB 和 Apache AGE 各选择 3 个紧凑案例。每个数据库前两个案例优先用于论文正文，第三个作为附录或替换案例。

统一展示格式为：`SETUP`、`QUERY`、`EXPECTED`、`ACTUAL`。为保持图面紧凑，省略数据库清理语句、连接代码和不影响核心行为的控制查询。

> 注意：这里的“Expected”来自官方语义、等价控制查询或多个参考实现的一致结果；“Actual”是报告中记录的目标版本实测结果。论文中应将“发现并复现”与“开发者已确认”分开表述。

## 1. Neo4j

### N1. Conditional branch order breaks type narrowing（正文推荐）

**Version:** Neo4j Community `2026.04.0`

```cypher
// SETUP: none

// QUERY
UNWIND [30,20] AS age
RETURN age
NEXT
WHEN age >= 25 THEN RETURN {v:age} AS a
ELSE RETURN [age] AS a
NEXT
FILTER a IS :: LIST<INTEGER>
RETURN size(a) AS s;

// EXPECTED: s = 1
// ACTUAL: SyntaxError: expected List<T>, but was Map
```

**Control:** Reversing the two branches succeeds and returns `s = 1`; the equivalent `CASE` expression also succeeds.

**Suggested caption:** *Neo4j rejects a list operation after an explicit list type guard depending only on conditional-branch order.*

**Source:** `C:/Users/Administrator/Desktop/pipeline/graph-bug-fuzz-mvp/issue_reports/submitted/neo4j/2026-5-26/neo4j_conditional_query_branch_order_may_break_type_predicate_narrowing.md`

### N2. `duration_between()` violates documented day-only semantics（正文推荐）

**Version:** Neo4j Community `2026.04.0`

```cypher
// SETUP: none

// QUERY
RETURN duration_between(
  date('2026-02-01'), date('2026-03-02')
) AS d;

// EXPECTED: P29D
// ACTUAL:   P1M1D
```

**Control:** `duration.inDays(...)` returns `P29D`, while legacy `duration.between(...)` returns the same `P1M1D` as the faulty GQL alias.

**Suggested caption:** *Neo4j evaluates the GQL `duration_between()` function using legacy year/month/day semantics instead of the documented day-only semantics.*

**Source:** `C:/Users/Administrator/Desktop/pipeline/graph-bug-fuzz-mvp/issue_reports/submitted/neo4j/2026-5-27/neo4j_duration_between_on_date_inputs_may_ignore_documented_gql_day_only_semantics.md`

### N3. Vector ordering disagrees with direct comparison（附录备选）

**Version:** Neo4j Community `2026.04.0`

```cypher
// SETUP: none

// QUERY
RETURN vector([1.0,2.0],2,FLOAT32)
       < vector([2.0,1.0],2,FLOAT32) AS x;

// EXPECTED: true (consistent with documented/order-by ordering)
// ACTUAL:   null
```

**Control:** `ORDER BY`, `min()`, and `max()` deterministically order the same two vectors with `[1.0,2.0]` first.

**Suggested caption:** *Neo4j orders vector values in sorting and aggregation but returns `null` for the corresponding direct comparison.*

**Source:** `C:/Users/Administrator/Desktop/pipeline/graph-bug-fuzz-mvp/issue_reports/submitted/neo4j/2026-5-27/neo4j_vector_values_may_have_defined_ordering_in_order_by_but_null_in_comparison_operators.md`

## 2. Memgraph

### M1. Duplicate string literals in `IN` duplicate result rows（正文推荐）

**Version:** Memgraph `3.9.0`

```cypher
// SETUP
CREATE (:Person {name:'Alice'}),
       (:Person {name:'Bob'}),
       (:Person {name:'Charlie'});

// QUERY
MATCH (q:Person)
WHERE q.name IN ['Alice', 'Alice', 'Bob']
RETURN q.name AS name ORDER BY name;

// EXPECTED: Alice, Bob
// ACTUAL:   Alice, Alice, Bob
```

**Control:** Removing the duplicate literal returns the correct two rows; duplicated numeric literals do not exhibit the same behavior.

**Suggested caption:** *Memgraph emits a duplicate result row when a string value is repeated in the right operand of `IN`.*

**Source:** `issue_reports/submitted/memgraph/2026-5-7/memgraph_in_predicate_with_duplicate_string_literals_may_duplicate_matching_rows.md`

### M2. Null predicate nullifies an entire list comprehension（正文推荐）

**Version:** Memgraph `3.9.0`, reverified on `3.10.1`

```cypher
// SETUP
CREATE (a:Person {name:'Alice'})
       -[:KNOWS]->(p:Person {name:'Charlie', age:35}),
       (a)-[:LIKES]->(i:Item {name:'Widget'});

// QUERY
MATCH (a:Person {name:'Alice'})-[:KNOWS]->(p),
      (a)-[:LIKES]->(i)
RETURN [x IN [p,i] WHERE x.age > 25 | x.name] AS names;

// EXPECTED: ['Charlie']
// ACTUAL:   null
```

**Control:** Adding `age:0` to the `Item`, or using `x.age IS NOT NULL`, returns `['Charlie']` correctly.

**Suggested caption:** *Memgraph propagates a null element predicate to the whole list comprehension instead of filtering that element out.*

**Source:** `issue_reports/submitted/memgraph/2026-5-17/memgraph_list_comprehension_filter_may_return_null_when_predicate_is_null_for_some_elements.md`

### M3. Point equality differs between `WHERE` and projection（附录备选）

**Version:** Memgraph Community `5.9.0`

```cypher
// SETUP
CREATE (:City {name:'London', lat:51.5074, lon:-0.1278}),
       (:City {name:'Paris',  lat:48.8566, lon: 2.3522});

// QUERY
MATCH (a:City), (b:City)
WHERE a.name <> b.name
  AND point({latitude:a.lat, longitude:a.lon}) =
      point({latitude:b.lat, longitude:b.lon})
RETURN a.name, b.name;

// EXPECTED: no rows
// ACTUAL:   Paris, London
```

**Control:** Projecting the exact same equality expression returns `false` for both cross-city pairs.

**Suggested caption:** *Memgraph accepts a false point-equality expression in `WHERE` although the same expression evaluates to `false` when projected.*

**Source:** `issue_reports/submitted/memgraph/2026-6-15/memgraph_point_equality_in_where_may_disagree_with_projected_equality.md`

## 3. FalkorDB

### F1. Anonymous patterns collapse parallel relationships（正文推荐）

**Version:** FalkorDB `4.18.3`, `4.18.4`

```cypher
// SETUP
CREATE (a:Person {name:'Alice'}),
       (b:Person {name:'Bob'}),
       (a)-[:KNOWS]->(b),
       (a)-[:KNOWS]->(b);

// QUERY
MATCH (:Person {name:'Alice'})-[:KNOWS]-(:Person {name:'Bob'})
RETURN count(*) AS cnt;

// EXPECTED: cnt = 2
// ACTUAL:   cnt = 1
```

**Control:** Binding the relationship as `r` and returning `count(r)` counts both relationships.

**Suggested caption:** *FalkorDB collapses parallel relationships when the relationship slot is anonymous.*

**Source:** `issue_reports/submitted/falkordb/2026-5-7/falkordb_anonymous_undirected_relationship_pattern_may_collapse_parallel_edges_between_same_endpoints.md`

### F2. A converging pattern reuses one relationship twice（正文推荐）

**Version:** FalkorDB `4.18.1`--`4.18.3`

```cypher
// SETUP
CREATE (:Person {name:'Alice'})
       -[:FRIEND]->(:Person {name:'Bob'});

// QUERY
MATCH (a:Person)-[:FRIEND]->(b:Person)<-[:FRIEND]-(c:Person)
RETURN a.name, b.name, c.name;

// EXPECTED: no rows
// ACTUAL:   Alice, Bob, Alice
```

**Control:** Binding `r1` and `r2` and requiring `r1 <> r2` removes the fabricated matches.

**Suggested caption:** *FalkorDB allows one physical relationship to satisfy two relationship positions in a single pattern.*

**Source:** `issue_reports/submitted/falkordb/2026-4-29/falkordb_converging_pattern_may_reuse_same_relationship_twice.md`

### F3. A self-loop-contributed variable-length path is lost（附录备选）

**Version:** FalkorDB `4.18.1`

```cypher
// SETUP
CREATE (d:Person {name:'Diana'}),
       (b:Person {name:'Bob'}),
       (d)-[:KNOWS]->(b),
       (d)-[:KNOWS]->(d);

// QUERY
MATCH p = (:Person {name:'Diana'})
          -[:KNOWS*1..2]->(:Person {name:'Bob'})
RETURN count(p) AS pathCount;

// EXPECTED: pathCount = 2
// ACTUAL:   pathCount = 1
```

**Control:** Replacing the self-loop route with a distinct intermediate node makes FalkorDB count both paths.

**Suggested caption:** *FalkorDB omits a valid bounded path whose first hop is a self-loop.*

**Source:** `issue_reports/submitted/falkordb/2026-4-29/falkordb_variable_length_path_may_ignore_self_loop_contributed_paths.md`

## 4. ArcadeDB

### A1. Literal `false` in `WHERE` is ignored（正文推荐）

**Version:** ArcadeDB `26.3.2`, `26.4.1-SNAPSHOT`, `26.4.2`

```cypher
// SETUP
CREATE (:Person {name:'Alice'});

// QUERY
MATCH (p:Person)
WHERE false
RETURN p.name;

// EXPECTED: no rows
// ACTUAL:   Alice
```

**Control:** `WHERE true = false`, `WHERE 1 = 0`, and `WHERE NOT true` correctly return no rows.

**Suggested caption:** *ArcadeDB ignores a literal-false filter while evaluating equivalent false expressions correctly.*

**Source:** `issue_reports/submitted/arcadedb/2026-5-1/arcadedb_where_false_literal_predicate_may_be_ignored.md`

### A2. Empty-list concatenation changes the result type（正文推荐）

**Version:** ArcadeDB `26.3.2` (fixed in tested `26.4.x` images)

```cypher
// SETUP: none

// QUERY
RETURN [] + 'Alice' + 'Charlie' + '1' AS r;

// EXPECTED: ['Alice', 'Charlie', '1']
// ACTUAL:   '[]AliceCharlie1'
```

**Control:** List-to-list concatenation and string-to-string concatenation both behave correctly.

**Suggested caption:** *ArcadeDB interprets empty-list-plus-scalar concatenation as string concatenation, changing both value and type.*

**Source:** `issue_reports/submitted/arcadedb/2026-5-1/arcadedb_empty_list_plus_scalar_concatenation_may_return_string_instead_of_list.md`

### A3. Correlated `COUNT {}` ignores outer variables（附录备选）

**Version:** ArcadeDB `26.4.1-SNAPSHOT`, `26.4.2`

```cypher
// SETUP
CREATE (:Person {name:'Alice', age:30}),
       (:Person {name:'Bob', age:25}),
       (:Person {name:'Charlie', age:35}),
       (:Person {name:'Diana', age:40});

// QUERY
MATCH (p:Person) WHERE p.age > 25
RETURN p.name,
       COUNT {
         MATCH (p2:Person) WHERE p2.age > p.age
         RETURN p2
       } AS older_count
ORDER BY p.name;

// EXPECTED: Alice:2, Charlie:1, Diana:0
// ACTUAL:   Alice:0, Charlie:0, Diana:0
```

**Control:** The equivalent `OPTIONAL MATCH` plus `count(p2)` formulation returns the expected values.

**Suggested caption:** *ArcadeDB evaluates a correlated count subquery as zero when its predicate accesses an outer variable.*

**Source:** `issue_reports/submitted/arcadedb/2026-4-25/arcadedb_correlated_count_subquery_with_outer_variable_access_may_evaluate_as_zero.md`

## 5. Apache AGE

### G1. `UNWIND [null]` makes `count(x)` count null（正文推荐）

**Version:** Apache AGE `1.7.0`, PostgreSQL `18.1`

```cypher
// SETUP: empty graph

// QUERY
UNWIND [null] AS x
RETURN count(x) AS c;

// EXPECTED: c = 0
// ACTUAL:   c = 1
```

**Control:** `RETURN count(null)` and `WITH null AS x RETURN count(x)` both return `0` on the same AGE instance.

**Suggested caption:** *Apache AGE counts a null value as non-null when it originates from `UNWIND`.*

**Source:** `issue_reports/submitted/age/old/age_unwind_null_count_counts_null_as_one.md`

### G2. `tail()` returns null for empty and singleton lists（正文推荐）

**Version:** Apache AGE `1.7.0`, PostgreSQL `18.1`

```cypher
// SETUP: empty graph

// QUERY
RETURN tail([1]) AS v;

// EXPECTED: []
// ACTUAL:   null
```

**Control:** `tail([1,2])` correctly returns `[2]`; `tail([])` and `tail([null])` reproduce the faulty `null` result.

**Suggested caption:** *Apache AGE returns `null` instead of an empty list at the lower-length boundary of `tail()`.*

**Source:** `issue_reports/submitted/age/old/age_tail_returns_null_for_empty_or_singleton_list.md`

### G3. Map literals drop null-valued keys（附录备选）

**Version:** Apache AGE `1.7.0`, PostgreSQL `18.1`

```cypher
// SETUP: empty graph

// QUERY
RETURN {a:null} AS m;

// EXPECTED: {a:null}
// ACTUAL:   {}
```

**Control:** `keys({a:null})` returns `[]` on AGE rather than `['a']`, confirming semantic key loss rather than a display-only difference.

**Suggested caption:** *Apache AGE removes keys with null values from map literals.*

**Source:** `issue_reports/submitted/age/old/age_map_literal_drops_null_valued_keys.md`

## Recommended Figure Composition

正文不建议同时放 15 个案例。更紧凑的组合是两张多面板图：

1. **Semantic-result bugs:** N2、M1、F2、A1、G1。
2. **Boundary and advanced-feature bugs:** N1、M2、F3、A3、G2。

每个 panel 只保留 6--12 行核心代码，并把 `EXPECTED` 与 `ACTUAL` 放在末尾。控制查询和完整环境移至正文描述或附录。这样既能覆盖所有数据库，也能同时体现表达式语义、行语义、路径语义、子查询语义和 null/boundary 语义五类缺陷。
