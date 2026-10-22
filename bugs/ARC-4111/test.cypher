CREATE (a:Person {name:'Alice'}),
       (b:Person {name:'Bob'}),
       (c:Person {name:'Charlie'}),
       (a)-[:KNOWS]->(b),
       (b)-[:KNOWS]->(c);

MATCH (p1:Person {name:'Alice'}), (p2:Person {name:'Bob'})
RETURN [(p1)-[:KNOWS*1..3]->(p2) | 'X'] AS vals;
