CREATE (:Traveler {name:'Alice'});
CREATE (:Town {name:'London', population: 8900000});

MERGE (p:Traveler {name:'Alice'})
WITH p
UNWIND [1, 2, 3] AS i
MERGE (p)-[:VISITS]->(c:Town {name:'London'})
ON MATCH SET c.population = 9000000
RETURN i, c.population AS population
ORDER BY i;
