MATCH (n) DETACH DELETE n;

CREATE (:Person {name:'Alice'});

MATCH (n:Person)
CREATE (n)-[:KNOWS]->(:Person {name:'Bob'})
RETURN DISTINCT n.name AS name
UNION
MATCH (n:Person)
RETURN DISTINCT n.name AS name;
