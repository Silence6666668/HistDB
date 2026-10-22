CREATE (:Person {name:'Alice', age:30})-[:WORKS_AT]->(:Company {name:'TechCorp'})-[:LOCATED_IN]->(:City {name:'Metropolis'});

MATCH (p:Person {age: 30})-[:WORKS_AT]->(c:Company)
OPTIONAL MATCH (c)-[:LOCATED_IN]->(city:City)
RETURN p.name AS person_name, c.name AS company_name, city.name AS city_name
LIMIT 3;
