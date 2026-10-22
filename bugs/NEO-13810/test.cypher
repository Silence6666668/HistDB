MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Bob', age:25})-[:HAS_DOG]->(:Dog {name:'Rex'});

CYPHER 25
MATCH (person:Person)
CALL (person) {
  MATCH (person)-[:HAS_DOG]->(dog:Dog)
  RETURN dog.name AS petName
}
RETURN count(*) AS outerCnt,
       COUNT {
         MATCH (person)-[:HAS_DOG]->(dog:Dog)
         RETURN dog.name AS petName
       } AS numPets;
