CYPHER 25
CREATE (:A {name:'na'})-[r:R]->(:B {name:'nb'})
RETURN r:$any(['R', null]) AS x;
