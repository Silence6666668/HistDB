MATCH (n) DETACH DELETE n;

CREATE (:Person {name:'Alice'})-[:FRIEND_OF {tags:['friend']}]->(:Person {name:'Bob'});

MATCH (n:Person {name:'Alice'}), (m:Person {name:'Bob'})
WHERE ANY(rel IN [(n)-[r:FRIEND_OF]->(m) | r] WHERE 'friend' IN rel.tags)
RETURN n.name AS person1, m.name AS person2;
