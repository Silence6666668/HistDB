MATCH (n) DETACH DELETE n;

CREATE (a:Person {name:'Alice', age:30}),
       (b:Person {name:'Bob', age:25}),
       (c:Person {name:'Charlie', age:35}),
       (a)-[:KNOWS {since:2020}]->(b),
       (b)-[:KNOWS {since:2019}]->(c);

MATCH (a:Person {name:'Alice'})-[r:KNOWS]->(b:Person)
WITH a, collect(r) AS rels
MATCH path = (a)-[:KNOWS*1..2]->(c:Person)
RETURN length(path) AS len, size(rels) AS relationCount
ORDER BY len;
