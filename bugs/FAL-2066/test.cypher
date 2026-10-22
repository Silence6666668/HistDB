CREATE (a:Person {name: 'Alice', age: 30}),
       (b:Person {name: 'Bob', age: 25}),
       (c:City {name: 'NYC'}),
       (d:City {name: 'LA'}),
       (a)-[:LIVES_IN]->(c),
       (b)-[:LIVES_IN]->(d);

OPTIONAL MATCH (p:Person)-[:LIVES_IN]->(c:City)
WITH p, c
ORDER BY p.age DESC
WITH head(collect(p)) AS oldestPerson
OPTIONAL MATCH (oldestPerson)-[:LIVES_IN]->(city:City)
RETURN oldestPerson.name AS name, city.name AS cityName;
