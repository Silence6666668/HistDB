CREATE (:Person {name:'Alice'}),
       (:Person {name:'Bob'}),
       (:Person {name:'Charlie'});

MATCH (p:Person)
WHERE any(x IN ['Alice'] WHERE x = p.name)
RETURN p.name AS name
ORDER BY name;
