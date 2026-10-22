MATCH (n) DETACH DELETE n;

CREATE (:Person {name:'Alice', scores:[85,92,78]});

MATCH (p:Person {name:'Alice'})
OPTIONAL MATCH (p)-[:HAS_FRIEND]->(f:Person)
WITH p, collect(f) AS friends
RETURN p.scores[toInteger(avg(size(friends)))] AS selectedScore;
