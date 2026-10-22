CREATE (:A {id: 1}),
       (:B {id: 2}),
       (:C {id: 3});

MATCH (n:A|B)
RETURN n.id AS id
ORDER BY id;
