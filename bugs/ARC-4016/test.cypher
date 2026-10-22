MATCH (n) DETACH DELETE n;

CREATE (:Person {name:'Alice', age:30});

MATCH (p:Person {name:'Alice'})
UNWIND [1, 2, 3] AS i
SET p.age = p.age + i
WITH DISTINCT p
RETURN p.age AS age;
