"""The snapshot builder's parsing and join, on small in-memory inputs (no download)."""

import importlib.util
from pathlib import Path

import pytest

from leadgen.places import read_snapshot

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "build_places_snapshot.py"
script_spec = importlib.util.spec_from_file_location("build_places_snapshot", SCRIPT_PATH)
assert script_spec is not None and script_spec.loader is not None
build_places_snapshot = importlib.util.module_from_spec(script_spec)
script_spec.loader.exec_module(build_places_snapshot)

GazetteerPlace = build_places_snapshot.GazetteerPlace
PlacePopulation = build_places_snapshot.PlacePopulation

# The real file pads every field with trailing spaces, so the parser must strip them.
GAZETTEER_SAMPLE = (
    "USPS\tGEOID\tANSICODE\tNAME\tLSAD\tFUNCSTAT\tALAND\tAWATER\tALAND_SQMI\tAWATER_SQMI"
    "\tINTPTLAT\tINTPTLONG        \n"
    "CA\t0656000\t02411379\tPasadena city\t25\tA\t59470461\t366889\t22.962\t0.142"
    "\t34.160602\t-118.137954        \n"
    "HI\t1571550\t02630047\tUrban Honolulu CDP\t57\tS\t156938609\t2500000\t60.5\t0.9"
    "\t21.324347\t-157.847640     \n"
)


def test_parse_gazetteer_strips_padding_and_reads_coordinates() -> None:
    places = build_places_snapshot.parse_gazetteer(GAZETTEER_SAMPLE)

    assert places == [
        GazetteerPlace("0656000", "CA", "Pasadena city", 34.160602, -118.137954),
        GazetteerPlace("1571550", "HI", "Urban Honolulu CDP", 21.324347, -157.84764),
    ]


def test_parse_tigerweb_page_reads_feature_attributes() -> None:
    page = {
        "features": [
            {"attributes": {"GEOID": "0656000", "BASENAME": "Pasadena", "POP100": 138699}},
            {"attributes": {"GEOID": "1571550", "BASENAME": "Urban Honolulu", "POP100": 350964}},
        ]
    }

    assert build_places_snapshot.parse_tigerweb_page(page) == [
        PlacePopulation("0656000", "Pasadena", 138699),
        PlacePopulation("1571550", "Urban Honolulu", 350964),
    ]


def test_parse_tigerweb_page_raises_on_a_service_error() -> None:
    with pytest.raises(RuntimeError, match="TIGERweb query failed"):
        build_places_snapshot.parse_tigerweb_page({"error": {"code": 400, "message": "bad"}})


@pytest.mark.parametrize(
    ("official_name", "base_name", "expected_type"),
    [
        ("Pasadena city", "Pasadena", "city"),
        ("Urban Honolulu CDP", "Urban Honolulu", "CDP"),
        ("Juneau city and borough", "Juneau", "city and borough"),
        ("Carson City", "Carson City", "place"),
    ],
)
def test_place_type_is_the_suffix_after_the_base_name(
    official_name: str, base_name: str, expected_type: str
) -> None:
    assert (
        build_places_snapshot.place_type_from_official_name(official_name, base_name)
        == expected_type
    )


def test_join_keeps_places_at_or_above_the_floor_with_coordinates_sorted_by_geoid() -> None:
    gazetteer = [
        GazetteerPlace("0656000", "CA", "Pasadena city", 34.160602, -118.137954),
        GazetteerPlace("0644000", "CA", "Los Angeles city", 34.019394, -118.410825),
        GazetteerPlace("0600135", "CA", "Acalanes Ridge CDP", 37.9, -122.08),
        GazetteerPlace("0600170", "CA", "Adin CDP", 41.19, -120.94),
    ]
    populations = [
        PlacePopulation("0656000", "Pasadena", 138699),
        PlacePopulation("0644000", "Los Angeles", 3898747),
        PlacePopulation("0600135", "Acalanes Ridge", 4999),  # one below the floor
        PlacePopulation("7816300", "Charlotte Amalie", 8194),  # no Gazetteer coordinates
    ]

    rows = build_places_snapshot.join_places(gazetteer, populations, minimum_population=5000)

    assert [(row.geoid, row.name, row.place_type, row.population) for row in rows] == [
        ("0644000", "Los Angeles", "city", 3898747),
        ("0656000", "Pasadena", "city", 138699),
    ]


def test_join_gives_consolidated_governments_their_everyday_name() -> None:
    gazetteer = [
        GazetteerPlace(
            "4752006", "TN", "Nashville-Davidson metropolitan government (balance)", 36.17, -86.78
        )
    ]
    populations = [
        PlacePopulation("4752006", "Nashville-Davidson metropolitan government (balance)", 689447)
    ]

    rows = build_places_snapshot.join_places(gazetteer, populations)

    assert (rows[0].name, rows[0].place_type) == ("Nashville", "consolidated city-county")


def test_written_snapshot_is_read_back_by_the_loader(tmp_path: Path) -> None:
    rows = build_places_snapshot.join_places(
        [GazetteerPlace("0656000", "CA", "Pasadena city", 34.160602, -118.137954)],
        [PlacePopulation("0656000", "Pasadena", 138699)],
    )
    snapshot_path = tmp_path / "places.csv"

    build_places_snapshot.write_snapshot(rows, snapshot_path)
    loaded = read_snapshot(snapshot_path)

    assert [(place.geoid, place.name, place.population, place.latitude) for place in loaded] == [
        ("0656000", "Pasadena", 138699, 34.160602)
    ]
