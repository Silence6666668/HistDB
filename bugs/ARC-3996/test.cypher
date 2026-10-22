CREATE (:Person {name:'Alice'});

WITH 1 AS x
CALL db.labels() YIELD label
RETURN x, label
LIMIT 3;
