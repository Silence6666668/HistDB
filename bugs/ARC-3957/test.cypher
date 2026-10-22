CREATE (:Person {name:'Alice'}),
       (:Person {name:'Bob'}),
       (:Person {name:'Charlie'});

MATCH (a:Person {name:'Alice'}), (b:Person {name:'Bob'})
CREATE (a)-[:KNOWS]->(b);

MATCH (p:Person)
RETURN p.name AS person,
       COLLECT { MATCH (p)-[:KNOWS]->(f:Person) RETURN f.name } AS friendNames
ORDER BY person;
