MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice'}), (:Person {name:'Bob'}), (:Person {name:'Charlie'});
MATCH (a:Person {name:'Alice'}), (b:Person {name:'Bob'}), (c:Person {name:'Charlie'})
CREATE (a)-[:KNOWS]->(b), (a)-[:FRIEND]->(c);

MATCH ()-[r:KNOWS]->()
WITH r
MATCH (x:Person)-[r]->(y)
RETURN x.name AS x, y.name AS y
ORDER BY x, y;
