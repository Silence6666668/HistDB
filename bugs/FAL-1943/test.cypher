CREATE (p:Person {name: 'Alice', age: 30}),
       (p2:Person {name: 'Bob', age: 25}),
       (p3:Person {name: 'Charlie', age: 35});

MATCH (p:Person)
WITH p
LIMIT 2
CALL {
  WITH p
  DELETE p
  RETURN 1 AS deletedCount
}
RETURN deletedCount
ORDER BY deletedCount;
