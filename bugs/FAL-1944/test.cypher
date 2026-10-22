MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice'})-[:FRIEND]->(:Person {name:'Bob'})<-[:FRIEND]-(:Person {name:'Charlie'});

MATCH (a:Person)-[:FRIEND]->(b:Person)<-[:FRIEND]-(c:Person)
RETURN a.name AS a, b.name AS b, c.name AS c
ORDER BY a, c;
