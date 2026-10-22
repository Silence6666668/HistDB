CREATE (:Person {name:'Alice', age:30}),
       (:Person {name:'Bob', age:25}),
       (:Person {name:'Charlie', age:35}),
       (:Person {name:'Diana', age:40});

MATCH (p:Person)
WHERE p.age > 25
RETURN p.name AS name,
       COLLECT {
         MATCH (p2:Person)
         WHERE p2.age > p.age
         RETURN p2.name
       } AS xs
ORDER BY name;
