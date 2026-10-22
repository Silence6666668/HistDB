MATCH (n) DETACH DELETE n;

CREATE (:Person {name:'Bob', age:30, role:'Employee'}), (:Person {name:'X', city:'Berlin'}), (:Person {name:'Y', city:'Paris'}), (:Person {name:'Z', city:'London'});

MATCH (p:Person {name:'Bob'})
CALL {
  MATCH (p:Person)
  WHERE p.city IS NOT NULL
  RETURN p.city AS location
}
RETURN p.name AS outerName, location
ORDER BY location;
