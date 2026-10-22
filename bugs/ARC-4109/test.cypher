CREATE (:Person {name:'Alice'})-[:KNOWS]->(:Person {name:'Bob'})-[:KNOWS]->(:Person {name:'Charlie'});

MATCH (p:Person)-[r:KNOWS]-()
WHERE (p)-[r:KNOWS]->()<-[r:KNOWS]-()
RETURN count(*) AS cnt;
