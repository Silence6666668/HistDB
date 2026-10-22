MATCH (n) DETACH DELETE n;

CREATE (:Person {name:'Alice'})-[:KNOWS]->(:Person {name:'Bob'})-[:KNOWS]->(:Person {name:'Charlie'});

MATCH (p:Person)-[:KNOWS]-(q:Person)
WITH p
WHERE size([(p)-[:KNOWS]-(r:Person) WHERE r.name STARTS WITH 'A' | r]) > 0
RETURN DISTINCT p.name AS p
ORDER BY p;
