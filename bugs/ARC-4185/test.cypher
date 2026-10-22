CREATE (a:Node {name:'A'})-[:REL]->(b:Node {name:'B'});

MATCH p = ()-[*]->()
WITH relationships(p) AS rels
FOREACH (r IN rels | DELETE endNode(r) DELETE r)
MATCH (n:Node)
RETURN n.name AS name
ORDER BY name;
