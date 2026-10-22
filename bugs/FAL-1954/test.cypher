MATCH (n) DETACH DELETE n;
CREATE (:Person {id: 1}), (:Person {id: 2}), (:Person {id: 3});

MATCH (n:Person)
RETURN n.id AS id, EXISTS((n)-[:KNOWS]->()) AS has_relationship
ORDER BY id;
