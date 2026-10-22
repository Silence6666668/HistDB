CYPHER 25
MATCH (n:N {case: 'slot'}) DETACH DELETE n;
CREATE (n:N {case: 'slot', name: 'A'}), (n)-[:R]->(n);

CYPHER 25
MATCH p = (n:N {case: 'slot'})-[:R]->(n:N)
CALL (n, p) {
  FILTER true
  SKIP 1
  FOR f IN [0, 2, -1]
  UNWIND ['x', n.name] AS u
  WITH n AS n, f AS f
  OFFSET 1
  FOR g IN [true]
  SKIP 0
  RETURN ALL f AS sq OFFSET 0
}
LET v = length(p)
RETURN sq AS out0;
