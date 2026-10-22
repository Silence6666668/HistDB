MATCH (n) DETACH DELETE n;

CREATE (a:Node {name:'Alice'})-[:KNOWS]->(b:Node {name:'Bob'}),
       (a)-[:FRIEND]->(c:Node {name:'Charlie'}),
       (b)-[:KNOWS]->(c);

MATCH ()-[r:KNOWS|FRIEND]-()
RETURN count(*) AS cnt,
       size(collect(DISTINCT r)) AS distinct_cnt;
