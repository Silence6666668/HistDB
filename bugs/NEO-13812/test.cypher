CREATE (n:Person {name: 'Alice', age: 30})
DELETE n
MERGE (n)-[:KNOWS {since: 2020, trust: 5}]->(n)
CREATE (m:Person {name: 'Bob'});
