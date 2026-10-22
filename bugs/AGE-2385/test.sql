SELECT create_graph('fuzz_graph');

SELECT * FROM cypher('fuzz_graph', $$
  RETURN sum(null) AS v
$$) AS (v agtype);
