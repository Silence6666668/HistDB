CREATE (:Person {name:'Alice'}), (:Person {name:'Bob'});
MATCH (a:Person {name:'Alice'}), (b:Person {name:'Bob'})
CREATE (a)-[:KNOWS]->(b);

MATCH (n)
WHERE ()-[:KNOWS]-() XOR n.name = 'Alice'
RETURN n.name
LIMIT 5;
