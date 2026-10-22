SELECT create_graph('fuzz_graph');

SELECT * FROM cypher('fuzz_graph', $$
  RETURN [x IN [null,1] WHERE x IS NULL] AS v
$$) AS (v agtype);
