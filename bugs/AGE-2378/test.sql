SELECT * FROM cypher('fuzz_graph', $$
  CREATE (a:Person {name: 'Alice', age: 30}),
         (b:Person {name: 'Bob', age: 25}),
         (c:Person {name: 'Charlie', age: 35}),
         (a)-[:KNOWS]->(b),
         (a)-[:KNOWS]->(c)
$$) AS (v agtype);

SELECT * FROM cypher('fuzz_graph', $$
  MATCH (p:Person)
  OPTIONAL MATCH (p)-[:KNOWS]->(friend:Person)
  WHERE EXISTS { (friend)-[:KNOWS]->(:Person) }
  RETURN p.name AS name, friend.name AS friend
  ORDER BY name, friend
$$) AS (name agtype, friend agtype);
