CREATE (a:Person {name:'Alice'}),
       (b:Person {name:'Bob'}),
       (c:Person {name:'Charlie'}),
       (a)-[:KNOWS {step: 1}]->(b),
       (b)-[:KNOWS {step: 2}]->(c),
       (c)-[:KNOWS {step: 3}]->(a);

MATCH path = (:Person {name:'Alice'})-[:KNOWS*3..3]->(:Person {name:'Alice'})
RETURN [n IN nodes(path) | n.name] AS pathNames,
       [rel IN relationships(path) | rel.step] AS stepOrder;
