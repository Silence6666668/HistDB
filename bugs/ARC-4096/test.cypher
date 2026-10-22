MATCH (n) DETACH DELETE n;

CREATE (a:Person {name:'Alice'}), (b:Person {name:'Bob'}), (c:Person {name:'Charlie'}), (a)-[:KNOWS]->(b), (b)-[:KNOWS]->(c);

MATCH (a:Person)-[r1:KNOWS]-(b:Person)-[r2:KNOWS]-(c:Person)
RETURN a.name AS a, b.name AS b, c.name AS c, r1 = r2 AS sameRel
ORDER BY a, b, c;
