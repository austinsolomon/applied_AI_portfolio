"""US Census places: the public city list a market expands into.

`places` holds every US place with at least 5,000 people in the 2020 Census, from the packaged
snapshot `data/us_places_2020.csv` (built by `scripts/build_places_snapshot.py` from the Census
Gazetteer and the Census Bureau's TIGERweb service). `expand_market` turns a market's center and
radius into the places inside it, largest first, each labelled "City, ST". The list comes from
public data rather than a model, so it never contains invented or ambiguous towns.
"""

import csv
from dataclasses import dataclass
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path

import psycopg
from psycopg.rows import class_row

PACKAGED_SNAPSHOT = files("leadgen") / "data" / "us_places_2020.csv"
SNAPSHOT_CENSUS_YEAR = 2020
SNAPSHOT_COLUMNS = ["geoid", "state", "name", "place_type", "population", "latitude", "longitude"]

DEFAULT_MIN_POPULATION = 5_000
DEFAULT_MAX_PLACES = 40


class PlacesSnapshotError(Exception):
    """The snapshot file does not have the expected columns."""


class MarketNotFound(LookupError):
    """No market has this id."""


@dataclass(frozen=True)
class PlaceRow:
    geoid: str
    state: str
    name: str
    place_type: str
    population: int
    latitude: float
    longitude: float


@dataclass(frozen=True)
class NearbyPlace:
    """A place inside a market's radius."""

    geoid: str
    name: str
    state: str
    place_type: str
    population: int
    distance_km: float

    @property
    def label(self) -> str:
        """Name and state, "Pasadena, CA": the state tells Kansas City, KS from Kansas City, MO."""
        return f"{self.name}, {self.state}"


def read_snapshot(path: Path | Traversable = PACKAGED_SNAPSHOT) -> list[PlaceRow]:
    with path.open("r", encoding="utf-8", newline="") as snapshot_file:
        reader = csv.DictReader(snapshot_file)
        if reader.fieldnames != SNAPSHOT_COLUMNS:
            raise PlacesSnapshotError(
                f"{path} has columns {reader.fieldnames}, expected {SNAPSHOT_COLUMNS}"
            )
        return [
            PlaceRow(
                geoid=row["geoid"],
                state=row["state"],
                name=row["name"],
                place_type=row["place_type"],
                population=int(row["population"]),
                latitude=float(row["latitude"]),
                longitude=float(row["longitude"]),
            )
            for row in reader
        ]


def load_places(
    connection: psycopg.Connection,
    snapshot_path: Path | Traversable = PACKAGED_SNAPSHOT,
    census_year: int = SNAPSHOT_CENSUS_YEAR,
) -> int:
    """Upsert the snapshot into `places` in one transaction; loading twice changes nothing.

    Returns the number of rows in the snapshot.
    """
    rows = read_snapshot(snapshot_path)
    with connection.transaction(), connection.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO places
                (geoid, state, name, place_type, population, latitude, longitude, census_year)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (geoid) DO UPDATE SET
                state       = excluded.state,
                name        = excluded.name,
                place_type  = excluded.place_type,
                population  = excluded.population,
                latitude    = excluded.latitude,
                longitude   = excluded.longitude,
                census_year = excluded.census_year,
                loaded_at   = now()
            """,
            [
                (
                    row.geoid,
                    row.state,
                    row.name,
                    row.place_type,
                    row.population,
                    row.latitude,
                    row.longitude,
                    census_year,
                )
                for row in rows
            ],
        )
    return len(rows)


def expand_market(
    connection: psycopg.Connection,
    market_id: int,
    radius_km: float | None = None,
    min_population: int = DEFAULT_MIN_POPULATION,
    max_places: int = DEFAULT_MAX_PLACES,
) -> list[NearbyPlace]:
    """Places within `radius_km` of the market's center, largest population first.

    `radius_km` defaults to the market's own service radius. Ties in population break by distance,
    then GEOID, so the order is stable.
    """
    market = connection.execute(
        "SELECT center_latitude, center_longitude, service_radius_km FROM markets "
        "WHERE market_id = %s",
        (market_id,),
    ).fetchone()
    if market is None:
        raise MarketNotFound(f"no market with id {market_id}")
    center_latitude, center_longitude, service_radius_km = market
    effective_radius_km = float(service_radius_km) if radius_km is None else float(radius_km)

    with connection.cursor(row_factory=class_row(NearbyPlace)) as cursor:
        return cursor.execute(
            """
            SELECT geoid, name, state, place_type, population,
                   haversine_km(%(latitude)s, %(longitude)s, latitude, longitude) AS distance_km
            FROM places
            WHERE population >= %(min_population)s
              AND haversine_km(%(latitude)s, %(longitude)s, latitude, longitude) <= %(radius_km)s
            ORDER BY population DESC, distance_km, geoid
            LIMIT %(max_places)s
            """,
            {
                "latitude": center_latitude,
                "longitude": center_longitude,
                "min_population": min_population,
                "radius_km": effective_radius_km,
                "max_places": max_places,
            },
        ).fetchall()
