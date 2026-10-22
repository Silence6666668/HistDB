CYPHER 25
WITH null AS n
CALL (n) {
  SET n:L
  WITH *
  MATCH (m)
  WHERE false
  MERGE ()
}
RETURN 1 AS result;
