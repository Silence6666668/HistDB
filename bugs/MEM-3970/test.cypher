MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice'})-[:FRIEND_OF]->(:Person {name:'Bob'}), (:Person {name:'Charlie'});

MATCH (p:Person)
OPTIONAL MATCH (p)-[:FRIEND_OF]->(f:Person)
RETURN p.name, COUNT(f) AS friendCount
ORDER BY p.name;
