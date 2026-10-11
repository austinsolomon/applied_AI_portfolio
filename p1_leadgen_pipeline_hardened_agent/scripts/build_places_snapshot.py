"""Build `src/leadgen/data/us_places_2020.csv` from two public-domain US Census Bureau sources.

The 2020 Gazetteer place file has every place's official name, state and interior-point coordinates
but no population. The 2020 Census population per place comes from the Census Bureau's TIGERweb
REST service (the `api.census.gov` endpoint needs an API key; TIGERweb does not). The two are
joined by the 7-digit place GEOID (state FIPS + place FIPS) and places below the population floor
are dropped, which keeps the snapshot small enough to live in the repository.

Usage, from the project folder (free: public data, no key, no account):
    uv run python scripts/build_places_snapshot.py

Re-run it to refresh the snapshot; the output is deterministic for a given set of inputs.
"""

import csv
import io
import json
import sys
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

GAZETTEER_ZIP_URL = (
    "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2020_Gazetteer/"
    "2020_Gaz_place_national.zip"
)
GAZETTEER_FILENAME = "2020_Gaz_place_national.txt"
TIGERWEB_CENSUS_2020 = (
    "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/tigerWMS_Census2020/MapServer"
)
# Layer ids in that service: incorporated places and census designated places (unincorporated
# communities the Census Bureau counts as places, such as Paradise, NV or Arlington, VA).
TIGERWEB_PLACE_LAYERS = (26, 28)
TIGERWEB_PAGE_SIZE = 2000

MINIMUM_POPULATION = 5_000
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_PATH = PROJECT_ROOT / "src" / "leadgen" / "data" / "us_places_2020.csv"
SNAPSHOT_COLUMNS = ["geoid", "state", "name", "place_type", "population", "latitude", "longitude"]

# Consolidated city-county governments carry their legal name in both sources ("Nashville-Davidson
# metropolitan government (balance)"), which nobody searches for. These are the everyday names.
CONSOLIDATED_PLACE_TYPE = "consolidated city-county"
CONSOLIDATED_PLACE_NAMES = {
    "0947515": "Milford",  # Milford city (balance), CT
    "1303440": "Athens",  # Athens-Clarke County unified government (balance), GA
    "1304204": "Augusta",  # Augusta-Richmond County consolidated government (balance), GA
    "1321017": "Cusseta",  # Cusseta-Chattahoochee County unified government, GA
    "1349008": "Macon",  # Macon-Bibb County, GA
    "1836003": "Indianapolis",  # Indianapolis city (balance), IN
    "2146027": "Lexington",  # Lexington-Fayette urban county, KY
    "2148006": "Louisville",  # Louisville/Jefferson County metro government (balance), KY
    "3001675": "Anaconda",  # Anaconda-Deer Lodge County, MT
    "3011397": "Butte",  # Butte-Silver Bow (balance), MT
    "4732742": "Hartsville",  # Hartsville/Trousdale County, TN
    "4744382": "Lynchburg",  # Lynchburg, Moore County metropolitan government, TN
    "4752006": "Nashville",  # Nashville-Davidson metropolitan government (balance), TN
}
# When the official name has no suffix to strip (Carson City, NV; Princeton, NJ).
DEFAULT_PLACE_TYPE = "place"


@dataclass(frozen=True)
class GazetteerPlace:
    geoid: str
    state: str
    official_name: str  # as the Census prints it: "Pasadena city", "Urban Honolulu CDP"
    latitude: float
    longitude: float


@dataclass(frozen=True)
class PlacePopulation:
    geoid: str
    base_name: str  # the name without its legal/statistical suffix: "Pasadena"
    population: int


@dataclass(frozen=True)
class SnapshotRow:
    geoid: str
    state: str
    name: str
    place_type: str
    population: int
    latitude: float
    longitude: float


def parse_gazetteer(text: str) -> list[GazetteerPlace]:
    """Read the tab-delimited Gazetteer file. Fields carry trailing padding, so each is stripped."""
    rows = csv.DictReader(io.StringIO(text), delimiter="\t")
    places = []
    for row in rows:
        fields = {key.strip(): value.strip() for key, value in row.items()}
        places.append(
            GazetteerPlace(
                geoid=fields["GEOID"],
                state=fields["USPS"],
                official_name=fields["NAME"],
                latitude=float(fields["INTPTLAT"]),
                longitude=float(fields["INTPTLONG"]),
            )
        )
    return places


def parse_tigerweb_page(payload: dict) -> list[PlacePopulation]:
    """Read one page of a TIGERweb feature query (`features[].attributes`)."""
    if "error" in payload:
        raise RuntimeError(f"TIGERweb query failed: {payload['error']}")
    return [
        PlacePopulation(
            geoid=feature["attributes"]["GEOID"],
            base_name=feature["attributes"]["BASENAME"],
            population=int(feature["attributes"]["POP100"]),
        )
        for feature in payload["features"]
    ]


def place_type_from_official_name(official_name: str, base_name: str) -> str:
    """The suffix after the base name: "Pasadena city" with base name "Pasadena" → "city"."""
    if official_name.startswith(base_name) and len(official_name) > len(base_name):
        return official_name[len(base_name) :].strip()
    return DEFAULT_PLACE_TYPE


def everyday_name_and_type(place: GazetteerPlace, entry: PlacePopulation) -> tuple[str, str]:
    consolidated_name = CONSOLIDATED_PLACE_NAMES.get(place.geoid)
    if consolidated_name is not None:
        return consolidated_name, CONSOLIDATED_PLACE_TYPE
    return entry.base_name, place_type_from_official_name(place.official_name, entry.base_name)


def join_places(
    gazetteer: list[GazetteerPlace],
    populations: list[PlacePopulation],
    minimum_population: int = MINIMUM_POPULATION,
) -> list[SnapshotRow]:
    """Join by GEOID, keep places at or above the floor, order by GEOID for a stable file.

    A population row with no Gazetteer place (island territories) is left out: the Gazetteer is
    the list of places, the population source only sizes them.
    """
    population_by_geoid = {entry.geoid: entry for entry in populations}
    rows = []
    for place in gazetteer:
        entry = population_by_geoid.get(place.geoid)
        if entry is None or entry.population < minimum_population:
            continue
        name, place_type = everyday_name_and_type(place, entry)
        rows.append(
            SnapshotRow(
                geoid=place.geoid,
                state=place.state,
                name=name,
                place_type=place_type,
                population=entry.population,
                latitude=place.latitude,
                longitude=place.longitude,
            )
        )
    rows.sort(key=lambda row: row.geoid)
    return rows


def write_snapshot(rows: list[SnapshotRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as snapshot_file:
        writer = csv.writer(snapshot_file, lineterminator="\n")
        writer.writerow(SNAPSHOT_COLUMNS)
        for row in rows:
            writer.writerow(
                [
                    row.geoid,
                    row.state,
                    row.name,
                    row.place_type,
                    row.population,
                    f"{row.latitude:.6f}",
                    f"{row.longitude:.6f}",
                ]
            )


def download_gazetteer() -> list[GazetteerPlace]:
    with urllib.request.urlopen(GAZETTEER_ZIP_URL, timeout=120) as response:
        archive = zipfile.ZipFile(io.BytesIO(response.read()))
    return parse_gazetteer(archive.read(GAZETTEER_FILENAME).decode("utf-8"))


def download_populations(minimum_population: int = MINIMUM_POPULATION) -> list[PlacePopulation]:
    """Every place at or above the floor, from both place layers, paged by GEOID."""
    populations: list[PlacePopulation] = []
    for layer_id in TIGERWEB_PLACE_LAYERS:
        offset = 0
        while True:
            query = urllib.parse.urlencode(
                {
                    "where": f"POP100>={minimum_population}",
                    "outFields": "GEOID,BASENAME,POP100",
                    "orderByFields": "GEOID",
                    "returnGeometry": "false",
                    "resultOffset": offset,
                    "resultRecordCount": TIGERWEB_PAGE_SIZE,
                    "f": "json",
                }
            )
            with urllib.request.urlopen(
                f"{TIGERWEB_CENSUS_2020}/{layer_id}/query?{query}", timeout=120
            ) as response:
                payload = json.load(response)
            page = parse_tigerweb_page(payload)
            populations.extend(page)
            if not payload.get("exceededTransferLimit") and len(page) < TIGERWEB_PAGE_SIZE:
                break
            offset += len(page)
    return populations


def main() -> int:
    gazetteer = download_gazetteer()
    populations = download_populations()
    rows = join_places(gazetteer, populations)
    outside_gazetteer = len(populations) - len(rows)
    write_snapshot(rows, SNAPSHOT_PATH)
    size_kb = SNAPSHOT_PATH.stat().st_size / 1024
    print(
        f"{len(gazetteer)} Gazetteer places, {len(populations)} with population >= "
        f"{MINIMUM_POPULATION}, {outside_gazetteer} of those outside the Gazetteer "
        f"(island territories); wrote {len(rows)} rows ({size_kb:.0f} KB) to {SNAPSHOT_PATH}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
