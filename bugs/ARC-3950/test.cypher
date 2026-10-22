CREATE (:Order {id: 'ORD-001', orderDate: 20230101}),
       (:Order {id: 'ORD-002', orderDate: 20230102}),
       (:Order {id: 'ORD-003', orderDate: 20230103}),
       (:Item {name: 'ItemA'}),
       (:Item {name: 'ItemB'}),
       (:Item {name: 'ItemC'});

MATCH (o:Order {id:'ORD-001'}), (i:Item {name:'ItemA'})
CREATE (o)-[:CONTAINS]->(i);

MATCH (o:Order {id:'ORD-002'}), (i:Item {name:'ItemB'})
CREATE (o)-[:CONTAINS]->(i);

MATCH (o:Order {id:'ORD-003'}), (i:Item {name:'ItemC'})
CREATE (o)-[:CONTAINS]->(i);

MATCH (o:Order)
ORDER BY o.orderDate DESC
LIMIT 1
MATCH (o)-[:CONTAINS]->(i:Item)
RETURN o.id AS orderId, collect(i.name) AS items;
