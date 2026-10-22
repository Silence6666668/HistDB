MATCH (n) DETACH DELETE n;

CREATE (a:Person {name:'Alice'}), (b:Person {name:'Bob'}), (a)-[:KNOWS]->(b);

MATCH (a:Person {name:'Alice'})-[r:KNOWS]->(b:Person {name:'Bob'})
WITH a, b, r
MATCH path = (a)-[:KNOWS*]->(b)
RETURN count(r) AS rc;
