MATCH (n) DETACH DELETE n;
CREATE (:Person {name: 'Alice'}), (:Person {name: 'Bob'}), (:Person {name: 'Charlie'});

MATCH (p:Person)
CALL {
  OPTIONAL MATCH (p)-[:ACTED_IN]->(m)
  RETURN m AS m
}
RETURN p.name AS person, count(m) AS c
ORDER BY person;
