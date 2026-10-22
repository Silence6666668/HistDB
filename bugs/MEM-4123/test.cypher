MATCH (n) DETACH DELETE n;

CREATE (:Person {name:'Alice'}), (:Person {name:'Bob'}), (:Person {name:'Charlie'});

MATCH (q:Person)
WHERE q.name IN ['Alice', 'Alice', 'Bob']
RETURN q.name AS name
ORDER BY name;
