WITH null AS xs
RETURN reduce(s = 0, x IN xs | s + x) AS total;
