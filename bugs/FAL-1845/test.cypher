MATCH (n) DETACH DELETE n;

CREATE (a:Person {name:'Alice'})-[:WORKS_FOR]->(b:Person {name:'Bob'}),
       (c:Person {name:'Charlie'})-[:WORKS_FOR]->(b);

MATCH (employee:Person)-[:WORKS_FOR]->(p)
WITH employee, p
CALL {
  WITH employee, p
  MATCH (employee)-[r:WORKS_FOR]->(p)
  DELETE r
  RETURN count(r) AS deleted
}
RETURN employee.name AS employee_name, p.name AS manager_name, deleted
ORDER BY employee_name;
