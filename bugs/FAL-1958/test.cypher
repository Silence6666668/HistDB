MERGE (p:Person {name: 'Alice'})
MERGE (c:Company {name: 'TechCorp'})
MERGE (p)-[r:WORKS_AT]->(c)
SET r.since = 2020
RETURN p.name AS personName, c.name AS companyName, r.since AS since
UNION
MATCH (p:Person {name: 'Alice'})
OPTIONAL MATCH (p)-[r:WORKS_AT]->(c)
RETURN p.name AS personName, c.name AS companyName, r.since AS since;
