MATCH (n) DETACH DELETE n;

CREATE (j:Person {name:'John'}),
       (a:Person {name:'Anna'}),
       (g:Country {name:'Germany'}),
       (j)-[:KNOWS]->(a),
       (j)-[:VISITED]->(g);

MATCH (:Person {name:'John'})--(m)
CALL {
  WITH m
  MERGE (m)-[:LOCATED_IN]->(:City {name:'Paris'})
  RETURN m.name AS connected_name
}
RETURN connected_name
ORDER BY connected_name;
