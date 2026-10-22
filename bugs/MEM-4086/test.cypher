MATCH (n) DETACH DELETE n;
CREATE (p1:Person {name: 'Alice', age: 30}),
       (p2:Person {name: 'Bob', age: 25}),
       (p3:Person {name: 'Charlie', age: 35});
CREATE (p1)-[:KNOWS]->(p2), (p1)-[:KNOWS]->(p3);

MATCH (a:Person {name:'Alice'})
WHERE EXISTS {
  MATCH (a)-[:KNOWS]->(c:Person)
  WHERE c.age > 25
  RETURN count(c) AS n
}
RETURN a.name AS name;
