CREATE (a:Person {name: 'Alice'}),
       (b:Person {name: 'Bob'}),
       (c:Person {name: 'Charlie'}),
       (d:Person {name: 'Diana'}),
       (a)-[:KNOWS]->(b),
       (b)-[:KNOWS]->(c),
       (c)-[:KNOWS]->(d),
       (a)-[:KNOWS]->(c);

MATCH (s:Person {name:'Alice'}), (t:Person {name:'Diana'})
WITH s, t
MATCH p = (s)-[*3]->(t)
RETURN [x IN nodes(p) | x.name] AS names
ORDER BY length(p) DESC;
