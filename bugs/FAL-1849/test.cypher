MATCH (n) DETACH DELETE n;

CREATE (c:Country {name:'France'}), (p:Person {name:'John'}), (f:Person {name:'Harry'});

MATCH (p:Person {name:'John'}), (c:Country {name:'France'}) CREATE (p)-[:WORKING_IN]->(c);

MATCH (p:Person {name:'John'}), (f:Person {name:'Harry'}) CREATE (p)-[:FRIENDS_WITH]->(f);

MATCH (p:Person)-[:WORKING_IN]->(:Country)
CALL {
  WITH p
  MATCH (p)-[:FRIENDS_WITH]->(f:Person)
  RETURN f.name AS friend_name
  LIMIT 1
}
MERGE (p)-[:HAS_FRIEND]->(f:Person {name: friend_name})
RETURN p.name AS person_name, friend_name;
