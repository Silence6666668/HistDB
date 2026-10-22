SELECT * FROM cypher('fuzz_graph', $$
  CREATE (:Person {name:'Alice'}),
         (:Person {name:'Bob'})
$$) AS (v agtype);

SELECT * FROM cypher('fuzz_graph', $$
  MATCH (p:Person)
  WITH collect(p) AS people
  RETURN [person IN people | person.name] AS names
$$) AS (names agtype);
