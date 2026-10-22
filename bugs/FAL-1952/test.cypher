CREATE (p:Person {name: 'Alice', age: 30}),
       (c:City {name: 'London', population: 8900000}),
       (p)-[:LIVES_IN]->(c);

MATCH (n)
WITH n
OPTIONAL MATCH (n)-[r]->(m), (n)<-[s]-(o)
RETURN n.name AS n_name,
       r IS NULL AS r_null,
       m.name AS m_name,
       s IS NULL AS s_null,
       o.name AS o_name
ORDER BY n_name;
