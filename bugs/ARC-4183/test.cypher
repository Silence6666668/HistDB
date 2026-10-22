CREATE (:Person {name:'Alice'}),
       (:Person {name:'Bob'});

MATCH (n:Person)
WHERE id(n) >= 0
RETURN n.name AS name
ORDER BY name;
