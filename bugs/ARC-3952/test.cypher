CREATE (:Person {name:'Alice'}),
       (:Person {name:'Bob'}),
       (:Person {name:'Charlie'}),
       (:Person {name:'David'});

MATCH (a:Person {name:'Alice'}), (b:Person {name:'Bob'})
CREATE (a)-[:WORKS_WITH]->(b);

MATCH (c:Person {name:'Charlie'}), (d:Person {name:'David'})
CREATE (c)-[:KNOWS]->(d);

MATCH (p:Person)
RETURN p.name AS person,
       EXISTS { MATCH (p)-[:WORKS_WITH]->(:Person) } AS worksWith
ORDER BY person;
