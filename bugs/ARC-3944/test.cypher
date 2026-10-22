CREATE (:Person {name:'Alice'}),
       (:Person {name:'Bob'}),
       (:Person {name:'Charlie'});

MATCH (p:Person)
CALL (p) {
  UNWIND range(1, 2) AS i
  CREATE (:Clone {name: p.name, id: i})
}
RETURN p.name AS person
ORDER BY person;
