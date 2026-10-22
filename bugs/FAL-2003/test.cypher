MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice', age:30})-[:KNOWS]->(:Person {name:'Bob', age:25});

MATCH (v:Person {name:'Alice'})--(n:Person)
RETURN n.age AS age,
       [x IN collect(v.age) WHERE x > 20 | x + n.age] AS vals
ORDER BY age;
