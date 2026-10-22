MATCH (n) DETACH DELETE n;

CREATE (:Person {name:'Alice'});

WITH 1 AS x
CALL db.labels() YIELD label
RETURN x AS x
ORDER BY label
LIMIT 1;
