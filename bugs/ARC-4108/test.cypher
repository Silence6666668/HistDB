CREATE (n1:Person {name: 'Alice'}),
       (n2:Person {name: 'Bob'}),
       (n3:Person {name: 'Charlie'});

MATCH (n1:Person {name: 'Alice'}), (n2:Person {name: 'Bob'})
CREATE (n1)-[:KNOWS]->(n2);

MATCH (n2:Person {name: 'Bob'}), (n3:Person {name: 'Charlie'})
CREATE (n2)-[:KNOWS]->(n3);

MATCH (a)-[b]->(c)
WHERE b:KNOWS
RETURN count(b) AS cnt;
