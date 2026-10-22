MATCH (n) DETACH DELETE n;
CREATE (:Person {name:'Alice'}), (:Person {name:'Bob'});
OPTIONAL MATCH nextPath = (:Person)
CALL {
  WITH nextPath
  RETURN nextPath AS path
  UNION ALL
  WITH nextPath
  RETURN nextPath AS path
}
RETURN *;
