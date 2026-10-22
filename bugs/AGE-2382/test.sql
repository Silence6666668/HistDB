SELECT * FROM cypher('fuzz_graph', $$
  CREATE (:Person {name: 'Alice'})
$$) AS (v agtype);

SELECT * FROM cypher('fuzz_graph', $$
  MATCH (p:Person {name: 'Alice'})
  OPTIONAL MATCH (p)-[:FRIEND*0..1]-(f:Person)
  RETURN p.name AS person, f.name AS friend
$$) AS (person agtype, friend agtype);
