MATCH (n) DETACH DELETE n;

CREATE (a:Person {name:'Alice'}),
       (b:Person {name:'Bob'}),
       (c:Person {name:'Charlie'}),
       (a)-[:KNOWS {since:2020}]->(b),
       (b)-[:KNOWS {since:2019}]->(c),
       (a)-[:KNOWS {since:2021}]->(c);

RETURN [(p:Person)-[r:KNOWS]->(q:Person) | q.name] AS xs;
