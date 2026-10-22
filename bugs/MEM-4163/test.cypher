MATCH (n) DETACH DELETE n;

CREATE (a:Person {name:'Alice'})-[:KNOWS]->(p:Person {name:'Charlie', age:35}),
       (a)-[:LIKES]->(i:Item {name:'Widget'});

MATCH (a:Person {name:'Alice'})-[:KNOWS]->(p:Person),
      (a)-[:LIKES]->(i:Item)
RETURN [x IN [p, i] WHERE x.age > 25 | x.name] AS names;
