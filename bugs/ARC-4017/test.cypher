CREATE (:Person {name:'Alice', age:30}),
       (:Person {name:'Bob', age:25}),
       (:Person {name:'Charlie', age:35});

MATCH (n:Person)
MATCH (m:Person)
WHERE n.age > m.age
SET n:Employee
RETURN n.name AS name
ORDER BY name;
