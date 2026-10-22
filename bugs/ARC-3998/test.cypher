MATCH (n) DETACH DELETE n;

CREATE (:Person {name:'Alice'}), (:Company {name:'TechCorp', industry:'Technology'});

MATCH (p:Person {name:'Alice'})
MERGE (p)-[r:WORKS_AT {since:2020}]->(c:Company)
WITH c
MATCH (company:Company)
RETURN c.name AS merged_company_name,
       c.industry AS merged_industry,
       count(company) AS total_companies;
