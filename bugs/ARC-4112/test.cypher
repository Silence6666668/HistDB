MATCH (n) DETACH DELETE n;

CREATE (:Person {name:'Alice'});

MATCH (p:Person)
WHERE false
RETURN p.name AS name;
