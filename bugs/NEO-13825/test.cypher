WITH 0 AS x
CALL (x) {
  UNWIND [] AS y
  FILTER false
  UNWIND [] AS z
  RETURN 0 AS r
}
RETURN r;

EXPLAIN
WITH 0 AS x
CALL (x) {
  UNWIND [] AS y
  FILTER false
  UNWIND [] AS z
  RETURN 0 AS r
}
RETURN r;
