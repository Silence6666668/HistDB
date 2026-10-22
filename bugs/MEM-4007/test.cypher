MATCH (n) DETACH DELETE n;
CREATE (:Person {name: 'Alice', age: 30});

MATCH (p:Person)
CALL {
  WITH p
  RETURN -100 AS amount
  UNION ALL
  OPTIONAL MATCH (:Person)-[:OWES]->(p)
  RETURN 150 AS amount
}
RETURN p.name AS person, max(amount) AS maxAmount
ORDER BY person;
