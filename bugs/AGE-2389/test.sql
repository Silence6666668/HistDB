SELECT * FROM cypher('fuzz_graph', $$
  CREATE (:Person {name:'Alice'}),
         (:Person {name:'Bob'})
$$) AS (v agtype);

SELECT * FROM cypher('fuzz_graph', $$
  MATCH (a:Person)
  CREATE (new:Person {name:'Zoe'})
  CREATE (a)-[:FRIEND]->(new)
  RETURN a.name AS a_name
$$) AS (a_name agtype);
