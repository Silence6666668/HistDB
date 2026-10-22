CYPHER 25
MATCH (n:N {case: 'arraycopy'}) DETACH DELETE n;

CREATE (a:N {case: 'arraycopy', id: 1, name: 'A', v: 1}),
       (b:N {case: 'arraycopy', id: 2, name: 'B', v: 2}),
       (a)-[:R]->(b);

CYPHER 25
WITH 1 AS x
MATCH SHORTEST 1 (n:N {case: 'arraycopy', id: 1})-[:R]->(m:N {case: 'arraycopy', id: 2})
WHERE COUNT { RETURN x } = 1 AND (COUNT { RETURN x } % -0.0) IS NOT :: STRING
RETURN n;
