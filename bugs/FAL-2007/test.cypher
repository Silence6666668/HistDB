MATCH (n) DETACH DELETE n;
CREATE (a:Person {name:'Alice'}),
       (b:Person {name:'Bob'}),
       (a)-[:KNOWS]->(b),
       (a)-[:KNOWS]->(b);

MATCH (:Person {name:'Alice'})-[:KNOWS]-(:Person {name:'Bob'})
RETURN count(*) AS cnt;
