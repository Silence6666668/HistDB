MATCH (n) DETACH DELETE n;
CREATE (a:Person {name: 'Alice'}),
       (b:Person {name: 'Bob'}),
       (c:Person {name: 'Charlie'}),
       (a)-[:KNOWS]->(b),
       (b)-[:KNOWS]->(c),
       (c)-[:KNOWS]->(a);

MATCH (a:Person {name:'Alice'}), (b:Person {name:'Bob'})
RETURN [(a)-[:KNOWS]->(t) | t.name] AS ax,
       [(b)-[:KNOWS]->(t) | t.name] AS bx;
