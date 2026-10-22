MATCH (n) DETACH DELETE n;

CREATE (:Person {name:'Alice'}), (:Person {name:'Bob'});

MATCH (a:Person {name:'Alice'})
MATCH (b:Person {name:'Bob'})
WITH a, b
CALL {
  CREATE (a)-[:LIKES]->(b)
}
RETURN a.name AS name;
