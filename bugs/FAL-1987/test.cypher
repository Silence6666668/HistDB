MATCH (n) DETACH DELETE n;

CREATE (:Person {name:'Alice', tags:['friend', 'colleague']});

MATCH (a:Person)
WITH a
WHERE all(item IN ['friend', 'colleague'] WHERE item IN a.tags)
RETURN a.name AS name, a.tags AS tags;
