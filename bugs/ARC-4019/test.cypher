MATCH (n) DETACH DELETE n;

CREATE (:Person {name:'Alice'}), (:Person {name:'Bob'});

MATCH (n:Person)
WITH n
CREATE (:Temp {x:1})
RETURN n.name AS name
ORDER BY name;
