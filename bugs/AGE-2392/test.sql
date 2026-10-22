SELECT create_graph('expr_slice_bug');

SELECT * FROM cypher('expr_slice_bug', $$
  RETURN [1, 2, 3][1..null] AS v
$$) AS (v agtype);
