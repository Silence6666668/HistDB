MATCH (n) DETACH DELETE n;

CREATE (:Person {name:'Alice'}), (:Company {name:'TechCorp'});

MATCH (n:Person|Company)
RETURN n.name AS name
ORDER BY name;
