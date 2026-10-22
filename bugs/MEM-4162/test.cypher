MATCH (n) DETACH DELETE n;

CREATE (:Person {name:'Alice'}), (:Person {name:'Bob'}), (:Person {name:'Charlie'});

MERGE (e:Person)
MERGE (:Person {name:'Eve'})
RETURN e.name AS eName
ORDER BY eName;
