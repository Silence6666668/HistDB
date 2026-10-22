SELECT create_graph('fuzz_graph');

SELECT * FROM cypher('fuzz_graph', $$
  RETURN [x IN [null] | x + 1] AS v
$$) AS (v agtype);
