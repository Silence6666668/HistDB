CREATE (:Person {name:'Alice', age:30}),
       (:Person {name:'Charlie', age:35}),
       (:Company {name:'TechCorp', industry:'Technology'});

UNWIND [1, 2, 3] AS i
WITH 'Alice' AS personName, 30 AS personAge, i
MATCH (c:Company)
WHERE c.industry = 'Technology'
MERGE (p:Person {name: personName})-[r:WORKS_AT]->(c)
RETURN personName,
       personAge,
       c.name AS companyName,
       COUNT(*) AS rowCount,
       COUNT(r) AS relationshipCount;
