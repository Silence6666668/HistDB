SELECT create_graph('fuzz_graph');

SELECT * FROM cypher('fuzz_graph', $$
  RETURN {a: null} AS m
$$) AS (m agtype);
