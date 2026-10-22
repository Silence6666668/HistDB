MATCH (n) DETACH DELETE n;

CREATE (a:Person {id:1, name:'Alice'}), (c:City {name:'New York'}), (u:Country {name:'USA'}), (a)-[:KNOWS]->(c), (c)-[:LOCATED_IN]->(u);

MATCH (a:Person)-[:KNOWS]->(:City)-[:LOCATED_IN]->(b:Country)
RETURN a.name AS person_name, b.name AS country_name;
