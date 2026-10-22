MATCH (n) DETACH DELETE n;

CREATE (:Person {name:'Alice'}), (:Person {name:'Bob'}), (:Person {name:'Charlie'});

MATCH (p:Person {name:'Alice'}) RETURN p.name AS name LIMIT 1
UNION ALL
MATCH (p:Person)
CREATE (p)-[:FRIEND]->(:Person {name:'David'})
RETURN p.name AS name;
