CREATE (a:Person {name:'Alice'}), (b:Person {name:'Bob'})
WITH a, b
MATCH (a), (b)
RETURN a.name AS name, b.name AS name2;
