MATCH (n) DETACH DELETE n;

CREATE (:Node {id:1, name:'test'}), (:Node {id:2, name:'other'});

MATCH (n)
RETURN exists { RETURN n AS x } AS result
ORDER BY result
LIMIT 1;
