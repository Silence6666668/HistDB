SELECT create_graph('fuzz_graph');

SELECT * FROM cypher('fuzz_graph', $$
  UNWIND [null] AS x
  RETURN count(x) AS c
$$) AS (c agtype);
