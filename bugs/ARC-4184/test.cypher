CREATE (:Person {name:'Alice'});

MATCH (p:Person)
RETURN p {.name, upperName: toUpper(p.name)} AS proj;
