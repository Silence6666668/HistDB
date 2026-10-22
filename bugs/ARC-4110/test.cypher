CREATE (:Person {name:'Alice'}),
       (:Person {name:'Bob'});

MATCH (a:Person {name:'Alice'}), (b:Person {name:'Bob'})
CREATE (a)-[:KNOWS]->(b);

MATCH (p:Person)
WHERE ()-[]->()
RETURN p.name AS name
ORDER BY name;
