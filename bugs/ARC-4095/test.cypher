MATCH (n) DETACH DELETE n;

CREATE (:Person {name:'Alice'})<-[:KNOWS]-(:Person {name:'Bob'})-[:KNOWS]->(:Person {name:'Charlie'});

MATCH (s:Person)<-[:KNOWS]-(f:Person)-[:KNOWS]->(d:Person)
RETURN s.name AS s, f.name AS f, d.name AS d
ORDER BY s, f, d;
