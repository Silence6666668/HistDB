MATCH (n) DETACH DELETE n;

CREATE (:Person {name:'Alice'}), (:Person {name:'Bob'});

MATCH (a:Person {name:'Alice'}), (b:Person {name:'Bob'})
MERGE (a)-[:KNOWS {since:-1}]->(b)
RETURN a.name AS a, b.name AS b
UNION ALL
MATCH (a:Person)-[r:KNOWS]->(b:Person)
WHERE r.since < 0
RETURN a.name AS a, b.name AS b;
