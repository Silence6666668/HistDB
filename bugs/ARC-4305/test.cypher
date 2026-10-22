CREATE (:City {lat: 1.0, lon: 2.0});

MATCH (c:City)
RETURN point({latitude: c.lat, longitude: c.lon}) AS p;
