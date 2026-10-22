MATCH (n) DETACH DELETE n;

CREATE (:Person {name:'Alice'})-[:WORKS_FOR]->(:Person {name:'Bob'}), (:Person {name:'Charlie'});

MATCH (a:Person {name:'Alice'}), (c:Person {name:'Charlie'}) CREATE (a)-[:KNOWS]->(c);

MATCH (employee:Person)-[:WORKS_FOR]->(p)
OPTIONAL MATCH (employee)-[:KNOWS]->(friend)
RETURN employee.name AS employee, p.name AS boss, friend.name AS optionalFriend
ORDER BY employee.name;
