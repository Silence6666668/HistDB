CREATE (:MergeP {name:'Alice', score:10.0});
CREATE (:MergeP {name:'Bob', score:20.0});
CREATE (:MergeP {name:'Charlie', score:30.0});

MATCH (p:MergeP)
WITH p, p.score / 2.0 AS halfScore
MERGE (p)-[:FRIEND]->(f:MergeF {name:''})
ON CREATE SET f.score = halfScore + 5.0
RETURN p.name AS person_name, f.score AS friend_score
ORDER BY person_name;
