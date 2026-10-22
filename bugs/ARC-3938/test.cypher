CREATE (:Country {name: 'Germany'}),
       (:Country {name: 'United Kingdom'}),
       (:Person {name: 'Alice'}),
       (:Person {name: 'Bob'}),
       (:Person {name: 'Charlie'});

MATCH (p:Person {name: 'Alice'}), (c:Country {name: 'Germany'})
CREATE (p)-[:LIVING_IN]->(c);

MATCH (p:Person {name: 'Bob'}), (c:Country {name: 'United Kingdom'})
CREATE (p)-[:LIVING_IN]->(c);

MATCH (p:Person {name: 'Charlie'}), (c:Country {name: 'Germany'})
CREATE (p)-[:LIVING_IN]->(c);

MATCH (p:Person)
WHERE (p)-[:LIVING_IN]->(:Country {name: 'Germany'})
RETURN collect(p.name) AS people;
