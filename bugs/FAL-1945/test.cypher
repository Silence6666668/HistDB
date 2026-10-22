MATCH (n) DETACH DELETE n;

CREATE (a:Person {name:'Alice'})
CREATE (b:Person {name:'Bob'})
CREATE (a)-[:KNOWS {since:2020}]->(b)
WITH a, b
MATCH (a)-[r:KNOWS]->(b)
RETURN r.since AS since;
