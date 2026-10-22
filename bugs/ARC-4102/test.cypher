CREATE (:Person {name:'Alice', city:'New York'}),
       (:City {name:'New York', population:8000000});

MATCH (p:Person {city:'New York'})
OPTIONAL MATCH (p)-[:LIVES_IN]->(c:City)
WITH p, c
MATCH (c:City {population:8000000})
RETURN p.name AS p, c.name AS c;
