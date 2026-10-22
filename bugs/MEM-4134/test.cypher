MATCH (n) DETACH DELETE n;

CREATE (:Person {name:'Charlie'}), (:City {name:'New York'});

MATCH (p:Person {name:'Charlie'}), (c:City {name:'New York'})
CREATE (p)-[:WORKS_IN]->(c)
RETURN p.name AS name,
       [(p)-[:WORKS_IN]->(city) | city.name] AS cities;
