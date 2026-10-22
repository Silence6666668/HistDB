SELECT create_graph('fuzz_graph');

SELECT * FROM cypher('fuzz_graph', $$
  CREATE (a:Person {id: 1, name: 'Alice'}),
         (m:Person {id: 2, name: 'Bob'})
  WITH a, m
  MATCH (x:Person)
  RETURN a, m, x
$$) AS (a agtype, m agtype, x agtype);
