MATCH (n) DETACH DELETE n;

CREATE (a:Person {name:'Alice', age:30}), (b:Person {name:'Bob', age:25})
WITH a, b
MATCH (a), (b)
WHERE a.age > 25 AND b.age < 30
RETURN a.name AS a, b.name AS b;
