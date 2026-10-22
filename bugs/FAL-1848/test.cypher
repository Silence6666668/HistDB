MATCH (n) DETACH DELETE n;

CREATE (a:Person {name:'Alice'}),
       (b:Person {name:'Bob'}),
       (a)-[:FRIENDS_WITH]->(b),
       (a)-[:WORKS_WITH]->(b);

MATCH (p:Person {name:'Alice'})
MATCH (p)-[:FRIENDS_WITH|WORKS_WITH]->(friend)
WITH friend, count(*) AS connectionCount
RETURN friend.name AS friendName, connectionCount
ORDER BY friendName;
