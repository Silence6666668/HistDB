MATCH (n) DETACH DELETE n;

CREATE (d:Person {name:'Diana'}),
       (b:Person {name:'Bob'}),
       (d)-[:KNOWS]->(b),
       (d)-[:KNOWS]->(d)
WITH d, b
MATCH path = (d)-[:KNOWS*1..2]->(b)
RETURN count(path) AS pathCount;
