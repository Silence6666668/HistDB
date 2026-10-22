CREATE (:Person {name:'Alice'}),
       (:Person {name:'Bob'}),
       (:Person {name:'Charlie'}),
       (:Dog {name:'Rex'}),
       (:Dog {name:'Fido'});

MATCH (p:Person {name:'Alice'}), (d:Dog {name:'Rex'})
CREATE (p)-[:OWNS]->(d);

MATCH (p:Person {name:'Bob'}), (d:Dog {name:'Fido'})
CREATE (p)-[:OWNS]->(d);

MATCH (person:Person)
RETURN person.name AS name,
       COUNT { (p:Person {name: person.name})-[:OWNS]->(:Dog) } AS dogCount
ORDER BY name;
