MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice', age:30}),
       (:Person {name:'Bob', age:25}),
       (:Person {name:'Charlie', age:35});
MATCH (a:Person {name:'Alice'}), (b:Person {name:'Bob'}), (c:Person {name:'Charlie'})
CREATE (a)-[:KNOWS {since:2020}]->(b),
       (a)-[:KNOWS {since:2019}]->(c);

MATCH (p:Person {age: 30})
OPTIONAL MATCH (p)-[:KNOWS]->(friend)
WHERE EXISTS {
  MATCH (p)-[:KNOWS*1..2]-(other)
  WHERE other.name = friend.name
}
RETURN p.name AS personName, friend.name AS friendName
ORDER BY friendName;
