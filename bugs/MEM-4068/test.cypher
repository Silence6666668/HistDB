MATCH (n) DETACH DELETE n;
CREATE (:Person {name: 'Alice', age: 30}),
       (:Company {name: 'TechCorp'});

MERGE (p:Person {name: 'Alice'})
MERGE (c:Company {name: 'TechCorp'})
MERGE (p)-[r:WORKS_AT]->(c)
SET r.since = 2020
RETURN p.name AS person, c.name AS company, r.since AS since
UNION ALL
MATCH (p:Person {name: 'Alice'})
OPTIONAL MATCH (p)-[r:WORKS_AT]->(c)
RETURN p.name AS person, c.name AS company, r.since AS since;
