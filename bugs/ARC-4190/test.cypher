CREATE (:Person {name:'Alice'})-[:R1]->(:Mid {name:'M'})-[:R2]->(:City {name:'X'});

MATCH p = allShortestPaths((:Person)-[:R1|R2*]->(:City))
RETURN size(nodes(p)) AS pathLen;
