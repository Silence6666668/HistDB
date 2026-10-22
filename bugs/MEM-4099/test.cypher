MATCH (n) DETACH DELETE n;

CREATE (a:Person {name:'Alice'})-[:KNOWS]->(b:Person {name:'Bob'}),
       (a)-[:WORKS_AT]->(c:Company {name:'Acme'}),
       (b)-[:WORKS_AT]->(c);

MATCH (a:Person {name:'Alice'})-[:KNOWS]->(b:Person {name:'Bob'})
RETURN [n IN [a, b] | size([(n)-[:WORKS_AT]->(t) | t.name])] AS counts;
