CREATE (a:Person {name:'Alice', age:30}),
       (b:Person {name:'Bob', age:25}),
       (c:Person {name:'Charlie', age:35});

OPTIONAL MATCH (p:Person)-[:KNOWS]-(q:Person)
WITH p
WHERE p IS NOT NULL
RETURN p.name;
