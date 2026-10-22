CREATE (a:Person {name: 'Alice'}),
       (b:Person {name: 'Bob'}),
       (c:Person {name: 'Charlie'}),
       (a)-[:KNOWS {since: 2018}]->(b),
       (a)-[:KNOWS {since: 2020}]->(c);

MATCH (a:Person)-[r:KNOWS WHERE r.since < 2019]-(b)
WHERE NOT (b:NonexistentLabel)
RETURN DISTINCT a.name AS person, b.name AS friend, r.since AS knowsSince
ORDER BY knowsSince;
