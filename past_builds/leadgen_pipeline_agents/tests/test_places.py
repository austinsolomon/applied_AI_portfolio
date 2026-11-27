"""The packaged Census places snapshot, loading it, and expanding a market into nearby places."""

from pathlib import Path

import psycopg
import pytest

from leadgen.demo_seed import DEMO_MARKETS, seed_demo_data
from leadgen.places import (
    PACKAGED_SNAPSHOT,
    SNAPSHOT_COLUMNS,
    MarketNotFound,
    expand_market,
    load_places,
    read_snapshot,
)

LARGE_FILE_HOOK_LIMIT_BYTES = 500 * 1024
LOS_ANGELES_CENTER = (34.0522, -118.2437)
KANSAS_CITY_CENTER = (39.0997, -94.5786)


def add_market_at(
    connection: psycopg.Connection,
    name: str,
    state: str,
    center: tuple[float, float],
    radius_km: float = 50,
) -> int:
    row = connection.execute(
        """
        INSERT INTO markets (name, state, center_latitude, center_longitude, service_radius_km,
                             lead_goal)
        VALUES (%s, %s, %s, %s, %s, 10) RETURNING market_id
        """,
        (name, state, *center, radius_km),
    ).fetchone()
    assert row is not None
    return row[0]


@pytest.fixture
def places_connection(migrated_connection: psycopg.Connection) -> psycopg.Connection:
    load_places(migrated_connection)
    return migrated_connection


# --- the packaged snapshot file -------------------------------------------------------------


def test_snapshot_stays_under_the_large_file_limit() -> None:
    assert Path(str(PACKAGED_SNAPSHOT)).stat().st_size < LARGE_FILE_HOOK_LIMIT_BYTES


def test_snapshot_rows_are_well_formed_unique_and_sorted() -> None:
    places = read_snapshot()
    geoids = [place.geoid for place in places]

    assert 6_000 < len(places) < 8_000
    assert geoids == sorted(geoids) and len(set(geoids)) == len(geoids)
    for place in places:
        assert len(place.geoid) == 7 and place.geoid.isdigit()
        assert len(place.state) == 2 and place.state.isupper()
        assert place.name.strip() == place.name != ""
        assert place.population >= 5_000
        assert -90 <= place.latitude <= 90 and -180 <= place.longitude <= 180
        # Consolidated governments carry their everyday name, not the legal one.
        assert "(balance)" not in place.name and "government" not in place.name


def test_snapshot_header_is_the_documented_one() -> None:
    header = PACKAGED_SNAPSHOT.read_text(encoding="utf-8").splitlines()[0]

    assert header.split(",") == SNAPSHOT_COLUMNS


# --- loading ---------------------------------------------------------------------------------


def test_load_places_is_idempotent(migrated_connection: psycopg.Connection) -> None:
    first_count = load_places(migrated_connection)
    second_count = load_places(migrated_connection)
    stored = migrated_connection.execute("SELECT count(*) FROM places").fetchone()

    assert first_count == second_count
    assert stored is not None and stored[0] == first_count


def test_load_places_refreshes_a_changed_row(migrated_connection: psycopg.Connection) -> None:
    load_places(migrated_connection)
    migrated_connection.execute("UPDATE places SET population = 1 WHERE geoid = '0656000'")

    load_places(migrated_connection)
    population = migrated_connection.execute(
        "SELECT population FROM places WHERE geoid = '0656000'"
    ).fetchone()

    assert population == (138699,)


def test_haversine_matches_a_known_distance(migrated_connection: psycopg.Connection) -> None:
    # Los Angeles to New York City, a textbook great-circle distance of about 3,936 km.
    distance = migrated_connection.execute(
        "SELECT haversine_km(34.0522, -118.2437, 40.7128, -74.0060)"
    ).fetchone()

    assert distance is not None and abs(distance[0] - 3936) < 5


# --- expanding a market ----------------------------------------------------------------------


def test_pasadena_is_about_15_km_from_los_angeles(places_connection: psycopg.Connection) -> None:
    market_id = add_market_at(places_connection, "Los Angeles", "CA", LOS_ANGELES_CENTER)

    nearby = {place.label: place for place in expand_market(places_connection, market_id)}

    assert 14 < nearby["Pasadena, CA"].distance_km < 16.5
    assert nearby["Pasadena, CA"].population == 138699


def test_places_come_largest_first(places_connection: psycopg.Connection) -> None:
    market_id = add_market_at(places_connection, "Los Angeles", "CA", LOS_ANGELES_CENTER)

    nearby = expand_market(places_connection, market_id)
    populations = [place.population for place in nearby]

    assert nearby[0].label == "Los Angeles, CA"
    assert populations == sorted(populations, reverse=True)


def test_radius_defaults_to_the_market_radius_and_can_be_overridden(
    places_connection: psycopg.Connection,
) -> None:
    market_id = add_market_at(
        places_connection, "Los Angeles", "CA", LOS_ANGELES_CENTER, radius_km=20
    )

    within_market_radius = expand_market(places_connection, market_id, max_places=1000)
    within_12_km = expand_market(places_connection, market_id, radius_km=12, max_places=1000)

    assert all(place.distance_km <= 20 for place in within_market_radius)
    assert all(place.distance_km <= 12 for place in within_12_km)
    assert "Pasadena, CA" in {place.label for place in within_market_radius}
    assert "Pasadena, CA" not in {place.label for place in within_12_km}
    assert 0 < len(within_12_km) < len(within_market_radius)


def test_population_floor_drops_smaller_places(places_connection: psycopg.Connection) -> None:
    market_id = add_market_at(places_connection, "Los Angeles", "CA", LOS_ANGELES_CENTER)

    large_only = expand_market(places_connection, market_id, min_population=100_000)
    labels = {place.label for place in large_only}

    assert all(place.population >= 100_000 for place in large_only)
    assert "Pasadena, CA" in labels  # 138,699 people
    assert "Alhambra, CA" not in labels  # 82,868 people, 10 km away


def test_cap_limits_the_count_without_changing_the_order(
    places_connection: psycopg.Connection,
) -> None:
    market_id = add_market_at(places_connection, "Los Angeles", "CA", LOS_ANGELES_CENTER)

    first_five = expand_market(places_connection, market_id, max_places=5)
    default_cap = expand_market(places_connection, market_id)

    assert len(first_five) == 5
    assert len(default_cap) == 40
    assert first_five == default_cap[:5]


def test_state_label_separates_same_named_cities(places_connection: psycopg.Connection) -> None:
    market_id = add_market_at(
        places_connection, "Kansas City", "MO", KANSAS_CITY_CENTER, radius_km=30
    )

    labels = [place.label for place in expand_market(places_connection, market_id)]

    assert labels[0] == "Kansas City, MO"
    assert "Kansas City, KS" in labels
    assert "Overland Park, KS" in labels
    assert "Independence, MO" in labels


def test_every_demo_market_expands_to_itself_first(places_connection: psycopg.Connection) -> None:
    seed_demo_data(places_connection)
    market_ids = dict(places_connection.execute("SELECT name, market_id FROM markets").fetchall())

    for demo_market in DEMO_MARKETS:
        nearby = expand_market(places_connection, market_ids[demo_market.name])

        assert nearby[0].label == f"{demo_market.name}, {demo_market.state}", demo_market.name
        assert len(nearby) >= 5, demo_market.name


def test_unknown_market_is_reported(places_connection: psycopg.Connection) -> None:
    with pytest.raises(MarketNotFound):
        expand_market(places_connection, market_id=999_999)
