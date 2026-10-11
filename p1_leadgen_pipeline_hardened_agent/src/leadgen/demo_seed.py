"""Deterministic, clearly fictional demo data: eight US markets and their CRM leads.

Business names are invented, websites use the reserved `.example` domain, and phone numbers use
the 555-0100 to 555-0199 range reserved for fiction. A fixed random seed gives every machine the
same data. Lead counts per market are chosen so the markets show different demand gaps.
"""

import random
from dataclasses import dataclass

import psycopg

RANDOM_SEED = 2026


@dataclass(frozen=True)
class DemoMarket:
    name: str
    state: str
    center_latitude: float
    center_longitude: float
    area_code: str
    lead_goal: int
    available: int
    assigned: int
    converted: int
    closed: int
    do_not_contact: int


# Leads Available % = available ÷ (goal − assigned − converted), shown in the trailing comment.
DEMO_MARKETS = [
    DemoMarket("Phoenix", "AZ", 33.4484, -112.0740, "602", 70, 6, 8, 2, 5, 1),  # 10%
    DemoMarket("Los Angeles", "CA", 34.0522, -118.2437, "213", 120, 14, 32, 8, 6, 2),  # 18%
    DemoMarket("San Diego", "CA", 32.7157, -117.1611, "619", 60, 12, 16, 4, 4, 1),  # 30%
    DemoMarket("Austin", "TX", 30.2672, -97.7431, "512", 50, 20, 8, 2, 3, 0),  # 50%
    DemoMarket("Chicago", "IL", 41.8781, -87.6298, "312", 90, 33, 24, 6, 5, 1),  # 55%
    DemoMarket("Denver", "CO", 39.7392, -104.9903, "303", 40, 20, 12, 3, 2, 0),  # 80%
    DemoMarket("Seattle", "WA", 47.6062, -122.3321, "206", 50, 50, 8, 2, 4, 1),  # 125%, no gap
    DemoMarket("Miami", "FL", 25.7617, -80.1918, "305", 40, 9, 38, 7, 3, 1),  # goal met
]

NAME_FIRST_WORDS = [
    "Golden",
    "Cedar",
    "Harbor",
    "Willow",
    "Summit",
    "Bayside",
    "Lotus",
    "Juniper",
    "Coastal",
    "Sage",
    "Evergreen",
    "Maple",
    "Riverstone",
    "Sunrise",
    "Tranquil",
    "Blue Ridge",
    "Driftwood",
    "Copper",
    "Meadow",
    "Pacific",
]
NAME_SECOND_WORDS = [
    "Mobile Massage",
    "Bodywork",
    "In-Home Massage",
    "Massage Therapy",
    "Wellness Massage",
    "Healing Hands",
    "Sports Massage",
    "Touch Therapy",
]


class DemoSeedRefused(Exception):
    """The database already holds markets or leads, so demo data would mix with real data."""


def seed_demo_data(connection: psycopg.Connection) -> int:
    """Insert the demo markets and leads in one transaction. Returns the number of leads."""
    existing_rows = connection.execute(
        "SELECT (SELECT count(*) FROM markets) + (SELECT count(*) FROM crm_leads)"
    ).fetchone()
    if existing_rows and existing_rows[0] > 0:
        raise DemoSeedRefused("the database already has markets or leads; demo data was not added")

    random_generator = random.Random(RANDOM_SEED)
    lead_count = 0
    with connection.transaction():
        for market in DEMO_MARKETS:
            market_row = connection.execute(
                """
                INSERT INTO markets (name, state, center_latitude, center_longitude, lead_goal)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING market_id
                """,
                (
                    market.name,
                    market.state,
                    market.center_latitude,
                    market.center_longitude,
                    market.lead_goal,
                ),
            ).fetchone()
            if market_row is None:
                raise RuntimeError(f"inserting market {market.name} returned no id")
            market_id = market_row[0]
            for lead_index, status in enumerate(_statuses(market)):
                business_name, phone, email, website, google_place_id = _demo_lead(
                    market, lead_index, random_generator
                )
                connection.execute(
                    """
                    INSERT INTO crm_leads
                        (market_id, business_name, status, phone, email, website,
                         google_place_id, source)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 'demo_seed')
                    """,
                    (market_id, business_name, status, phone, email, website, google_place_id),
                )
                lead_count += 1
    return lead_count


def _statuses(market: DemoMarket) -> list[str]:
    return (
        ["available"] * market.available
        + ["assigned"] * market.assigned
        + ["converted"] * market.converted
        + ["closed"] * market.closed
        + ["do_not_contact"] * market.do_not_contact
    )


def _demo_lead(
    market: DemoMarket, lead_index: int, random_generator: random.Random
) -> tuple[str, str | None, str | None, str, str]:
    first_word = random_generator.choice(NAME_FIRST_WORDS)
    second_word = random_generator.choice(NAME_SECOND_WORDS)
    business_name = f"{first_word} {second_word} {lead_index + 1}"
    market_slug = market.name.lower().replace(" ", "")
    business_slug = f"{first_word}{second_word}".lower().replace(" ", "").replace("-", "")
    domain = f"{business_slug}{lead_index + 1}-{market_slug}.example"
    # Some leads have only a phone or only an email, as real CRM data does; never neither.
    phone = None if lead_index % 7 == 6 else f"({market.area_code}) 555-{100 + lead_index:04d}"
    email = None if lead_index % 5 == 4 and phone is not None else f"hello@{domain}"
    website = f"https://www.{domain}/"
    google_place_id = f"demo_{market_slug}_{lead_index + 1:03d}"
    return business_name, phone, email, website, google_place_id
