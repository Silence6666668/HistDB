CYPHER 25
MATCH (n:Missing) DETACH DELETE n;

CYPHER 25 runtime = slotted
OPTIONAL MATCH (n:Missing)-->(m)
RETURN DISTINCT coalesce(n.name, '') AS v ORDER BY v;
