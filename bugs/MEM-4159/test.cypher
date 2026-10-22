MATCH (n) DETACH DELETE n;

MATCH (n:MissingLabel)
RETURN count(*) + abs(-1) AS v;
