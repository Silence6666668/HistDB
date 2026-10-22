MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice', age:30});
CREATE (:Person {name:'Bob', age:25});
CREATE (:Person {name:'Charlie', age:35});

MATCH (p:Person)
OPTIONAL MATCH (p)-[r:FRIEND]->()
WHERE p.age > 25
WITH p, r
WHERE [x IN [r.since, r.strength] WHERE x > 5] IS NOT NULL
RETURN p.name AS name
ORDER BY name;
