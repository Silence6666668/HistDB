SELECT * FROM cypher('fuzz_graph', $$
  CREATE (:Country {name: 'Germany'}),
         (:Country {name: 'United Kingdom'}),
         (:Person {name: 'Anna'})
$$) AS (v agtype);

SELECT * FROM cypher('fuzz_graph', $$
  MATCH (a:Person {name:'Anna'}), (g:Country {name:'Germany'})
  CREATE (a)-[:LIVING_IN]->(g)
$$) AS (v agtype);

SELECT * FROM cypher('fuzz_graph', $$
  MATCH (person:Person)
  WHERE EXISTS {
    MATCH (person)-[:LIVING_IN*1..2]->(:Country)
    UNION
    MATCH (person)-[:WORKING_IN]->(:Country)
  }
  RETURN person.name AS name
$$) AS (name agtype);
