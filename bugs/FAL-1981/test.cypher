CREATE (:Person {name:'Alice'}), (:Person {name:'Bob'});

MATCH (a:Person {name:'Alice'}), (b:Person {name:'Bob'})
OPTIONAL MATCH (a), (b)
RETURN count(*) AS cnt;
