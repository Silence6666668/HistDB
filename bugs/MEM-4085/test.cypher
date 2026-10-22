MATCH (n) DETACH DELETE n;
CREATE (a:Location {name: 'PointA', lat: 40.7128, lon: -74.0060});
CREATE (b:Location {name: 'PointB', lat: 40.7580, lon: -73.9855});
CREATE (c:Location {name: 'PointC', lat: 40.7489, lon: -73.9680});
CREATE (d:Location {name: 'PointD', lat: 40.7549, lon: -73.9840});
CREATE (e:Location {name: 'PointE', lat: 40.7614, lon: -73.9776});

MATCH (p1:Location), (p2:Location)
WHERE p1 <> p2
  AND point.distance(
        point({latitude: p1.lat, longitude: p1.lon}),
        point({latitude: p2.lat, longitude: p2.lon})
      ) < 2000
RETURN p1.name AS p1, p2.name AS p2
ORDER BY p1, p2;
