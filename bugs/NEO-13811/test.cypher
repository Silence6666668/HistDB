UNWIND [{name:'Alice'}, {name:'Bob'}] AS p
MATCH (p:Person)
WHERE p.name IN ['Alice', 'Bob']
RETURN p.name AS name;
