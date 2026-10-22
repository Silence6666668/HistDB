MATCH (n) DETACH DELETE n;

CREATE (:Person {name:'Charlie'})
MATCH (n:Person)
RETURN count(*) AS c;
