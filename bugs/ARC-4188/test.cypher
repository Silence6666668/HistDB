CREATE (a:Person {name:'Alice'}),
       (b:Person {name:'Bob'}),
       (a)-[:FRIEND]->(b);

WITH 'Bob' AS fName
MATCH (p:Person)-[:FRIEND]->(:Person {name: fName})
RETURN p.name AS person
ORDER BY person;
