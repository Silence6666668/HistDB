CREATE (:Person {name:'Alice', age:30});
CREATE (:Person {name:'Bob', age:25});
CREATE (:Person {name:'Charlie', age:35});

MATCH (p:Person)
RETURN p.name AS name,
       reduce(total = 0, n IN collect(p.age) | total + n) AS total_age_sum
ORDER BY name;
