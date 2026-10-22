MATCH (n) DETACH DELETE n;

CREATE (a:Person {name: 'Alice', age: 30}),
       (b:Person {name: 'Bob', age: 25});

MATCH (a:Person {name: 'Alice'}), (b:Person {name: 'Bob'})
SET a.age = a.age + 1,
    b.age = b.age,
    a.age = a.age + 2
RETURN a.age AS aliceAge, b.age AS bobAge;
