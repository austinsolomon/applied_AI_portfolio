"""Provider-neutral source records persisted before enrichment or judgment."""

from dataclasses import asdict, dataclass, field

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


@dataclass
class RawLead:
    source_key: str
    business_name: str
    google_place_id: str | None = None
    phone: str | None = None
    email: str | None = None
    website: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    categories: list[str] = field(default_factory=list)
    seed_consensus: int = 1


def persist_leads(connection, run_id, market_id, source, leads, *, cell_id=None):
    inserted = 0
    with connection.transaction():
        for lead in leads:
            values = asdict(lead)
            values.update(run_id=run_id, market_id=market_id, source=source, cell_id=cell_id)
            values["categories"] = Jsonb(values["categories"])
            inserted += connection.execute(
                "INSERT INTO raw_leads(run_id,market_id,source,cell_id,source_key,business_name,"
                "google_place_id,phone,email,website,latitude,longitude,categories,seed_consensus) "
                "VALUES (%(run_id)s,%(market_id)s,%(source)s,%(cell_id)s,%(source_key)s,"
                "%(business_name)s,%(google_place_id)s,%(phone)s,%(email)s,%(website)s,"
                "%(latitude)s,%(longitude)s,%(categories)s,%(seed_consensus)s) "
                "ON CONFLICT (run_id,source,source_key) DO NOTHING",
                values,
            ).rowcount
    return inserted


def read_raw_leads(connection, run_id):
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            "SELECT * FROM raw_leads WHERE run_id=%s ORDER BY raw_lead_id", (run_id,)
        ).fetchall()
