CREATE (a:A {name: 'Alice', age: 30}),
       (b:B {name: 'Bob', age: 25}),
       (c:C {name: 'Charlie', age: 35}),
       (d:D {name: 'Diana', age: 28}),
       (e:E {name: 'Eve', age: 32});

CREATE (a)-[:KNOWS]->(b),
       (a)-[:KNOWS]->(c),
       (b)-[:KNOWS]->(d),
       (c)-[:KNOWS]->(e),
       (d)-[:KNOWS]->(e);

CREATE (a)-[:FRIEND]->(c),
       (b)-[:FRIEND]->(e);

MATCH (n:A)
OPTIONAL MATCH (n)-[:KNOWS]->(m)
WITH n, collect(m.name) AS friends
ORDER BY size([(n)-[:FRIEND]->(f) | f.name]) DESC
RETURN n.name AS name, n.age AS age, friends;
