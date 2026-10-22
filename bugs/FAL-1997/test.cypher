MATCH (n) DETACH DELETE n;

CREATE (:Person {name:'Alice', age:30})-[:KNOWS {since:2020}]->(:Person {name:'Bob', age:25});

MATCH (n:Person)
WHERE n.age > 20
WITH n
WHERE ()<-[:KNOWS {since:2020}]-()
RETURN n.name AS name
ORDER BY n.name;
