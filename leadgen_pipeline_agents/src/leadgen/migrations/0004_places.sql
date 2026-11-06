-- US Census places (2020), the public list a market expands into, and the distance function
-- that both market expansion and the sample grader's in-market check use.

CREATE TABLE places (
    -- 7-digit Census GEOID: 2-digit state FIPS + 5-digit place FIPS.
    geoid       char(7)          PRIMARY KEY CHECK (geoid ~ '^[0-9]{7}$'),
    state       char(2)          NOT NULL CHECK (state ~ '^[A-Z]{2}$'),
    name        text             NOT NULL CHECK (btrim(name) <> ''),
    -- city, town, village, borough, CDP (census designated place), consolidated city-county, ...
    place_type  text             NOT NULL CHECK (btrim(place_type) <> ''),
    population  integer          NOT NULL CHECK (population >= 0),
    latitude    double precision NOT NULL CHECK (latitude BETWEEN -90 AND 90),
    longitude   double precision NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    census_year smallint         NOT NULL,
    loaded_at   timestamptz      NOT NULL DEFAULT now()
);

CREATE INDEX places_population_idx ON places (population DESC);

-- Great-circle distance in kilometres between two points, by the haversine formula on a sphere of
-- the Earth's mean radius. Accurate to about 0.5% at these distances, which is plenty for a radius.
CREATE FUNCTION haversine_km(
    latitude_a double precision, longitude_a double precision,
    latitude_b double precision, longitude_b double precision
) RETURNS double precision
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
    SELECT 2 * 6371.0088 * asin(least(1.0, sqrt(
        power(sin(radians(latitude_b - latitude_a) / 2), 2)
        + cos(radians(latitude_a)) * cos(radians(latitude_b))
          * power(sin(radians(longitude_b - longitude_a) / 2), 2)
    )))
$$;
