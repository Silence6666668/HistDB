SELECT create_graph('expr_substring_bug');

SELECT * FROM cypher('expr_substring_bug', $$
  RETURN substring('abc', null, 1) AS v
$$) AS (v agtype);
