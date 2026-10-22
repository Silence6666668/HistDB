CREATE (:Node {score: 1});

MATCH (n:Node)
CALL {
  WITH n
  SET n.score = 2
  RETURN 1 AS ok
}
RETURN n.score AS score;
