CREATE (:Person {name:'Alice', age:30});

MATCH (p:Person)
DELETE p
RETURN p;
