SELECT * FROM cypher('fuzz_graph', $$
  CREATE (:Person {name: 'Alice'}),
         (:Person {name: 'Bob'}),
         (:Person {name: 'Charlie'})
$$) AS (v agtype);

SELECT * FROM cypher('fuzz_graph', $$
  MATCH (n:Person)
  RETURN n AS person
  ORDER BY person.name DESC
  LIMIT 2
$$) AS (person agtype);
