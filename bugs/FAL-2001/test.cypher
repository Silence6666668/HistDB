RETURN duration('P2Y') > duration('P1Y') AS gt,
       duration('P6M') > duration('P1Y') AS gt2,
       duration('P2Y') = duration('P2Y') AS eq;
