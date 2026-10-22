MATCH (n) DETACH DELETE n;

CREATE (:Person {name:'Alice', age:30}), (:Person {name:'Bob', age:25})
WITH count(*) AS dummy
MATCH (n:Person) WHERE n.age > 25
RETURN n.name AS name
UNION
MATCH (n:Person) WHERE n.age <= 25
RETURN n.name AS name;
