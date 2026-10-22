MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice', age:30, active:true}),
       (:Person {name:'Bob', age:25, active:false}),
       (:Person {name:'Charlie', age:35, active:true});

MATCH (p:Person)
WHERE p.active = true
WITH p
ORDER BY p.age DESC
LIMIT 2
WITH collect(p) AS activePeople
RETURN [person IN activePeople | {name: person.name, age: person.age}] AS vals;
