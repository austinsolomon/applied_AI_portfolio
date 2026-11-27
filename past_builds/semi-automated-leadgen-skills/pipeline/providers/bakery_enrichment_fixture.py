"""bakery_enrichment_fixture.py — what the offline contact-data services know.

THIS IS DATA, NOT LOGIC. The mock Apollo and Hunter providers in enrichment.py
look a bakery up here and return the fields this table has. Nothing is random, so
every run gives the same record and the same cost.

ONE ENTRY PER LISTING ROW, 25 ROWS IN SOURCING ORDER. Two rows repeat an
earlier bakery, as in the sourcing fixture:

  row 7   the same Google Maps place as row 1 (same place id, same lead id).
  row 16  a second listing of row 5 under a longer name: new place id, same
          phone and address. Source drops it. If a fork lets it through, the
          answers match row 5, because it is the same shop.

So 25 rows are 23 bakeries.

READING ONE ENTRY
  apollo   what the contact-data service returns for this bakery. None means
           Apollo has no answer for that field.
  hunter   what the email finder returns. Only a few entries have one: Hunter
           is asked only for the contact fields Apollo left open, so it never
           sees most bakeries. A row with no `hunter` key is a Hunter miss.

WHAT APOLLO KNOWS
  owner_name, owner_role, email, location_count   almost always. Two gaps:
      Morning Crumb: Apollo lists a baker on staff, not the owner.
      Tin Roof Bakery: Apollo has the manager's name but no email.
  ordering_platform   only 3 of 23 (Velvet Whisk, Poppyseed, Daily Dozen).
      Contact databases track people and companies, not how a bakery takes
      orders. The other 20 go to the research agent, whose recorded answers
      are in fixtures/research_agent_answers.json.

WHAT HUNTER KNOWS (asked second, only for the gaps)
  Tin Roof Bakery: the manager's email, on the bakery's own domain.
  Morning Crumb: the actual owner, with her name, role and email. The shop
      has no website, so her address is a personal mailbox, like the one
      Apollo has for the baker.
  Fig & Feather Bakery: nothing. Apollo's contact there is a shift lead, so
      Hunter is asked for the owner too, and misses. The shift lead stays.

EVERYTHING HERE IS MADE UP. Emails sit on the reserved `.example` domain, so
none of them can reach a real inbox.
"""

from __future__ import annotations

from typing import Any

BAKERY_ENRICHMENT_ROWS: tuple[dict[str, Any], ...] = (
    {
        "listing_row": 1,
        "lead_id": "apify-maps-72a168d9e318",
        "place_id": "fixture-place-001",
        "bakery_name": "Sunrise Bakery",
        "apollo": {
            "owner_name": "Rosa Delgado",
            "owner_role": "Owner",
            "email": "rosa@sunrise.example",
            "location_count": 1,
            "ordering_platform": None,
        },
    },
    {
        "listing_row": 2,
        "lead_id": "apify-maps-f0fe374ccec2",
        "place_id": "fixture-place-002",
        "bakery_name": "Riverbend Bread",
        "apollo": {
            "owner_name": "Tomas Whitaker",
            "owner_role": "Owner",
            "email": "tomas@riverbend-bread.example",
            "location_count": 1,
            "ordering_platform": None,
        },
    },
    {
        "listing_row": 3,
        "lead_id": "apify-maps-657921fbe661",
        "place_id": "fixture-place-003",
        "bakery_name": "Oak Street Bakes",
        "apollo": {
            "owner_name": "Dale Pruitt",
            "owner_role": "Owner",
            "email": "dale@oak-street-bakes.example",
            "location_count": 2,
            "ordering_platform": None,
        },
    },
    {
        "listing_row": 4,
        "lead_id": "apify-maps-9fd995a9cb11",
        "place_id": "fixture-place-004",
        "bakery_name": "Flour & Hearth",
        "apollo": {
            "owner_name": "Anjali Nair",
            "owner_role": "Co-owner",
            "email": "anjali.flour-and-hearth@mail.example",
            "location_count": 1,
            "ordering_platform": None,
        },
    },
    {
        "listing_row": 5,
        "lead_id": "apify-maps-b1376a14177b",
        "place_id": "fixture-place-005",
        "bakery_name": "Morning Crumb",
        "apollo": {
            "owner_name": "Wes Adeyemi",
            "owner_role": "Baker",
            "email": "wes.morning-crumb@mail.example",
            "location_count": 1,
            "ordering_platform": None,
        },
        "hunter": {
            "owner_name": "Nora Lindqvist",
            "owner_role": "Owner",
            "email": "nora.morning-crumb@mail.example",
        },
    },
    {
        "listing_row": 6,
        "lead_id": "apify-maps-cb488d4932cd",
        "place_id": "fixture-place-006",
        "bakery_name": "Bluebird Bakery",
        "apollo": {
            "owner_name": "Hannah Kowalski",
            "owner_role": "Co-owner",
            "email": "hannah@bluebird-bakery.example",
            "location_count": 1,
            "ordering_platform": None,
        },
    },
    {
        "listing_row": 7,
        "repeat_of_listing_row": 1,
        "lead_id": "apify-maps-72a168d9e318",
        "place_id": "fixture-place-001",
        "bakery_name": "Sunrise Bakery",
        "apollo": {
            "owner_name": "Rosa Delgado",
            "owner_role": "Owner",
            "email": "rosa@sunrise.example",
            "location_count": 1,
            "ordering_platform": None,
        },
    },
    {
        "listing_row": 8,
        "lead_id": "apify-maps-aaaa3e5131fb",
        "place_id": "fixture-place-008",
        "bakery_name": "Golden Wheat Bakery Cafe",
        "apollo": {
            "owner_name": "Trent Ashworth",
            "owner_role": "Corporate Office",
            "email": "partnerships@golden-wheat.example",
            "location_count": 12,
            "ordering_platform": None,
        },
    },
    {
        "listing_row": 9,
        "lead_id": "apify-maps-de70049ff199",
        "place_id": "fixture-place-009",
        "bakery_name": "Lantern Lane Pies",
        "apollo": {
            "owner_name": "Gus Hartley",
            "owner_role": "Owner",
            "email": "gus@lantern-lane-pies.example",
            "location_count": 1,
            "ordering_platform": None,
        },
    },
    {
        "listing_row": 10,
        "lead_id": "apify-maps-546f59b5e4a8",
        "place_id": "fixture-place-010",
        "bakery_name": "Velvet Whisk Patisserie",
        "apollo": {
            "owner_name": "Camille Fontaine",
            "owner_role": "Owner",
            "email": "camille@velvet-whisk.example",
            "location_count": 1,
            "ordering_platform": "delivery_marketplace",
        },
    },
    {
        "listing_row": 11,
        "lead_id": "apify-maps-62e64e5b88ed",
        "place_id": "fixture-place-011",
        "bakery_name": "Butter & Thyme Bakery",
        "apollo": {
            "owner_name": "Felipe Arroyo",
            "owner_role": "Owner",
            "email": "felipe@butter-and-thyme.example",
            "location_count": 4,
            "ordering_platform": None,
        },
    },
    {
        "listing_row": 12,
        "lead_id": "apify-maps-5cfae4b54a99",
        "place_id": "fixture-place-012",
        "bakery_name": "Sweet Thistle Bakeshop",
        "apollo": {
            "owner_name": "Imani Brooks",
            "owner_role": "Owner",
            "email": "imani@sweet-thistle.example",
            "location_count": 2,
            "ordering_platform": None,
        },
    },
    {
        "listing_row": 13,
        "lead_id": "apify-maps-e37b7bfc1eb6",
        "place_id": "fixture-place-013",
        "bakery_name": "Tiny Kiln Bakery",
        "apollo": {
            "owner_name": "Noelle Park",
            "owner_role": "Owner",
            "email": "noelle.tiny-kiln-bakery@mail.example",
            "location_count": 1,
            "ordering_platform": None,
        },
    },
    {
        "listing_row": 14,
        "lead_id": "apify-maps-ff2499df9fe9",
        "place_id": "fixture-place-014",
        "bakery_name": "Fig & Feather Bakery",
        "apollo": {
            "owner_name": "Jamie Ortiz",
            "owner_role": "Shift Lead",
            "email": "jamie.fig-and-feather-bakery@mail.example",
            "location_count": 1,
            "ordering_platform": None,
        },
    },
    {
        "listing_row": 15,
        "lead_id": "apify-maps-ae675c78b0a0",
        "place_id": "fixture-place-015",
        "bakery_name": "Tin Roof Bakery",
        "apollo": {
            "owner_name": "Luis Navarro",
            "owner_role": "General Manager",
            "email": None,
            "location_count": 1,
            "ordering_platform": None,
        },
        "hunter": {
            "owner_name": "Luis Navarro",
            "owner_role": "General Manager",
            "email": "luis@tin-roof-bakery.example",
        },
    },
    {
        "listing_row": 16,
        "repeat_of_listing_row": 5,
        "lead_id": "apify-maps-2b813a1ae99e",
        "place_id": "fixture-place-016",
        "bakery_name": "Morning Crumb Bakery & Cafe",
        "apollo": {
            "owner_name": "Wes Adeyemi",
            "owner_role": "Baker",
            "email": "wes.morning-crumb@mail.example",
            "location_count": 1,
            "ordering_platform": None,
        },
        "hunter": {
            "owner_name": "Nora Lindqvist",
            "owner_role": "Owner",
            "email": "nora.morning-crumb@mail.example",
        },
    },
    {
        "listing_row": 17,
        "lead_id": "apify-maps-c70ecea7805b",
        "place_id": "fixture-place-017",
        "bakery_name": "Poppyseed Bakehouse",
        "apollo": {
            "owner_name": "Tessa Aldana",
            "owner_role": "Owner",
            "email": "tessa@poppyseed-bakehouse.example",
            "location_count": 1,
            "ordering_platform": "website_builder_checkout",
        },
    },
    {
        "listing_row": 18,
        "lead_id": "apify-maps-34ca750a92da",
        "place_id": "fixture-place-018",
        "bakery_name": "Rye & Rosemary Bakery",
        "apollo": {
            "owner_name": "Sofia Marchetti",
            "owner_role": "Owner",
            "email": "sofia@rye-and-rosemary.example",
            "location_count": 1,
            "ordering_platform": None,
        },
    },
    {
        "listing_row": 19,
        "lead_id": "apify-maps-423d3ade609c",
        "place_id": "fixture-place-019",
        "bakery_name": "Honeycomb Oven",
        "apollo": {
            "owner_name": "Mei-Lin Chau",
            "owner_role": "Owner",
            "email": "mei-lin.honeycomb-oven@mail.example",
            "location_count": 1,
            "ordering_platform": None,
        },
    },
    {
        "listing_row": 20,
        "lead_id": "apify-maps-fbc7a8215dd1",
        "place_id": "fixture-place-020",
        "bakery_name": "Daily Dozen Donuts & Bakery",
        "apollo": {
            "owner_name": "Franchise Support Desk",
            "owner_role": "Corporate Office",
            "email": "franchise@daily-dozen.example",
            "location_count": 40,
            "ordering_platform": "restaurant_ordering_app",
        },
    },
    {
        "listing_row": 21,
        "lead_id": "apify-maps-a29a96a58a53",
        "place_id": "fixture-place-021",
        "bakery_name": "Grand Avenue Cake Hall",
        "apollo": {
            "owner_name": "Beatrice Langford",
            "owner_role": "Owner",
            "email": "beatrice@grand-avenue-cake-hall.example",
            "location_count": 1,
            "ordering_platform": None,
        },
    },
    {
        "listing_row": 22,
        "lead_id": "apify-maps-81270ebdc081",
        "place_id": "fixture-place-022",
        "bakery_name": "Little Anvil Bread Co.",
        "apollo": {
            "owner_name": "Omar Sayegh",
            "owner_role": "Owner",
            "email": "omar@little-anvil-bread.example",
            "location_count": 1,
            "ordering_platform": None,
        },
    },
    {
        "listing_row": 23,
        "lead_id": "apify-maps-6713fb0e5ad6",
        "place_id": "fixture-place-023",
        "bakery_name": "Dos Rios Panaderia",
        "apollo": {
            "owner_name": "Elena Villarreal",
            "owner_role": "Owner",
            "email": "elena@dos-rios-panaderia.example",
            "location_count": 1,
            "ordering_platform": None,
        },
    },
    {
        "listing_row": 24,
        "lead_id": "apify-maps-d4321a585a22",
        "place_id": "fixture-place-024",
        "bakery_name": "Porchlight Pastry",
        "apollo": {
            "owner_name": "Dev Malhotra",
            "owner_role": "Owner",
            "email": "dev.porchlight-pastry@mail.example",
            "location_count": 0,
            "ordering_platform": None,
        },
    },
    {
        "listing_row": 25,
        "lead_id": "apify-maps-08a0b0bfedca",
        "place_id": "fixture-place-025",
        "bakery_name": "Night Owl Bagels & Bakes",
        "apollo": {
            "owner_name": "Carmen Ibarra",
            "owner_role": "Owner",
            "email": "carmen@night-owl-bagels.example",
            "location_count": 1,
            "ordering_platform": None,
        },
    },
)
