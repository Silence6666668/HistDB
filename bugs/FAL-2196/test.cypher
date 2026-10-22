CREATE(),();

OPTIONAL MATCH(a)
MERGE(b{x:1,x:2})<-[:S]-(c)
ON MATCH SET a:L;
