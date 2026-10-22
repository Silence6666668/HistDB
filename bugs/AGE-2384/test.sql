SELECT create_graph('expr_tail_bug');

SELECT * FROM cypher('expr_tail_bug', $$
  RETURN tail([]) AS v
$$) AS (v agtype);
