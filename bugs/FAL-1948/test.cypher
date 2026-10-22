MATCH (n) DETACH DELETE n;
CREATE (p:Person {name: 'Alice', age: 30}),
       (c:Company {name: 'TechCorp', industry: 'Technology'}),
       (p)-[:WORKS_FOR]->(c),
       (p)-[:WORKS_FOR]->(c),
       (p)-[:WORKS_FOR]->(c);

MATCH (c:Company {name: 'TechCorp'})<-[r:WORKS_FOR]-(p:Person)
WITH c, count(*) AS cstar, count(p) AS cp, collect(p.name) AS names
RETURN c.name AS company_name, cstar, cp, names;
