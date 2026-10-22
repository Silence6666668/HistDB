MATCH (n:N) DETACH DELETE n;

CREATE (a:N {id: 1}),
       (x:N {id: 2}),
       (y:N {id: 3}),
       (x)-[:R]->(a),
       (a)-[:R]->(y);

CYPHER 25
MATCH (n:N {id: 1})
RETURN n AS a
NEXT
RETURN 1 AS s
UNION
MATCH ANY SHORTEST (x:N {id: 2})-[:R]-(a)-[:R]-(y:N {id: 3})
RETURN 2 AS s;

MATCH (n:N) DETACH DELETE n;
