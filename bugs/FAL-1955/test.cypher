CREATE (:Person {name:'Alice', age:30}),
       (:Person {name:'Bob', age:25}),
       (:Person {name:'Charlie', age:35});

UNWIND range(1, 5) AS i
CREATE (n:Person {id: i})
WITH collect(n) AS batchNodes
MATCH (p:Person)
WHERE p.name IN ['Alice', 'Bob']
MERGE (p)-[:KNOWS]->(new:Person {name:'NewFriend'})
WITH p, new
RETURN p.name AS personName, new.name AS newName;
