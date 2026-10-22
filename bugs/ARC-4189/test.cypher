CREATE (:Person {name:'Alice'}),
       (:Person {name:'Bob'});

MATCH (p:Person)
RETURN DISTINCT *;
