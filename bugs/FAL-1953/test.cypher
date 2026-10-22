CREATE (:Person {name:'Alice'})-[:FRIEND_OF]->(:Person);
CREATE (:Person {name:'Bob'})-[:WORKS_WITH]->(:Person {name:'Carol'});

MATCH (n:Person)
RETURN n.name AS nodeName,
       [(n)-[:FRIEND_OF]-(m) | m.name] AS friendNames
ORDER BY nodeName
LIMIT 10;
