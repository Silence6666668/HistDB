MATCH (n) DETACH DELETE n;
CREATE (a:Person {name: 'Alice'}),
       (b:Person {name: 'Bob'}),
       (c:Person {name: 'Charlie'}),
       (a)-[:FRIEND_OF]->(b),
       (a)-[:FRIEND_OF]->(c),
       (b)-[:FRIEND_OF]->(c),
       (c)-[:FRIEND_OF]->(a);

MATCH p = (:Person {name: 'Charlie'})-[:FRIEND_OF*]-(other:Person)
RETURN count(p) AS pathCount, count(DISTINCT other) AS distinctOthers;
