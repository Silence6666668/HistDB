CREATE (a:Node {id: 1}),
       (b:Node {id: 2});

MATCH (a:Node {id: 1})
OPTIONAL MATCH (a)-[:KNOWS]->(b:Node {id: 2})
WITH a, b
MERGE (a)-[:KNOWS]->(b)
RETURN a.id AS aid, b.id AS bid;
