CREATE (:Person {name:'Alice'})-[:KNOWS]->(:Person {name:'Bob'});

MATCH (p:Person)-[r:KNOWS]-()
WITH p, r
WHERE (p)-[r:KNOWS]->()<-[r:KNOWS]-()
RETURN p.name;
