CREATE (:A)-[:R]->(:B);

UNWIND [()-[]->() | 1] AS x
RETURN x;
