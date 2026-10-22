MATCH (n) DETACH DELETE n;

CREATE (:Person {name:'Alice', age:30}), (:Person {name:'Bob', age:25});

MATCH (n:Person {name:'Alice'}), (m:Person {name:'Alice'})
SET n.age = 35
RETURN n.age AS n_age, m.age AS m_age;
