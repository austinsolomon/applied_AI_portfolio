"""bakery_outreach_fixture.py — what the offline writer writes and what the
offline sender reports, per bakery.

WHY THIS FILE EXISTS. With no keys set, the outreach stage must give the same
result on every machine, and that result must be the one the course slides
show: nine qualified bakeries arrive, one draft is held by the lint, eight
emails are sent, five are delivered, two reply, and one meeting is confirmed. So nothing here is drawn at random. The writer
fills a template. The sender looks the outcome up.

THREE TEMPLATES, ONE PER CHANNEL THE BAKERY RUN USES. An email has a subject
line. An Instagram message has none. The second email campaign is shorter. Each
template takes the four placeholders in schema.OUTREACH_PLACEHOLDERS and
nothing else. They are written for the course's offer (OFFER below); a live run
with a key hands your own `--offer` to Claude instead.

`how_orders_work_today` IS THE ONE SPECIFIC, CHECKABLE REASON in each message.
It is an observation the writer makes about one bakery, not a column anybody
bought, so it lives here and not on the lead.

TWO DRAFTS ARE BAD ON PURPOSE, so the lint has something to catch on a fresh
run. One is shouting spam. The other is polite, and still has two links and a
promise in it. A lint that never fires teaches nothing.

EVERYTHING HERE IS MADE UP. Links sit on the reserved `.example` domain, so
none of them can reach a real site.
"""

from __future__ import annotations

import hashlib
from typing import Any

from ..schema import outreach_placeholder_values
from .copy import CHANNEL_MODEL

OFFER = "I build simple websites that let Chicago bakeries take orders online."

# The three lanes this seller uses. `linkedin_inmail` stays in CHANNEL_MODEL
# and still works with `--channel linkedin_inmail`; a bakery record has no
# LinkedIn column, so the bakery run leaves that lane empty.
BAKERY_RUN_CHANNELS = ("email",)

MESSAGE_TEMPLATES: dict[str, dict[str, str]] = {
    "email": {
        "subject": "Online orders for {bakery_name}",
        "body": (
            "Hi {owner_first_name},\n\n"
            "I saw {bakery_name} has {review_count} reviews on Google Maps, "
            "and that {how_orders_work_today}.\n\n"
            "I build simple websites for Chicago bakeries that let customers "
            "order and pay online, so the phone can stay quiet during the "
            "morning rush. A site like that is $500, one time.\n\n"
            "Would it help if I sent a two-minute example made with your menu?\n\n"
            "Jordan Ellis"
        ),
    },
    "instagram_dm": {
        "subject": "",
        "body": (
            "Hi {owner_first_name}, I build simple websites for Chicago bakeries "
            "that let customers order and pay online. I noticed that "
            "{how_orders_work_today}. Would a quick example made with your menu "
            "be useful?\n\n"
            "Jordan Ellis"
        ),
    },
    "email_campaign_2": {
        "subject": "A quick example for {bakery_name}",
        "body": (
            "Hi {owner_first_name},\n\n"
            "A short note. I can put together a two-minute example of online "
            "ordering for {bakery_name}, using your own menu, at no cost to you.\n\n"
            "Would that be useful?\n\n"
            "Jordan Ellis"
        ),
    },
}

# The first draft of every message is the email form: it is the longest of the
# three and the only one with room for a link, so it is the strictest thing to
# put in front of the lint.
FIRST_DRAFT_CHANNEL = "email"

# Used for a bakery this table has never seen, such as one from your own search.
DEFAULT_HOW_ORDERS_WORK_TODAY = "customers still order by phone or in person"

# The counter-example. Shouting, three links, six exclamation marks and a
# handful of the lint's spam words.
SHOUTING_SPAM_DRAFT = {
    "subject": "FREE GUARANTEED ORDERS — ACT NOW",
    "body": (
        "Hi {owner_first_name}!!! We GUARANTEE risk-free online orders for "
        "{bakery_name}. See https://a.example https://b.example https://c.example "
        "This is a LIMITED TIME offer!!!"
    ),
}

# The quieter counter-example. It reads like a fair message. It still carries
# two links and the word "guaranteed", and either one is enough to hold it.
POLITE_DRAFT_WITH_TWO_LINKS_AND_A_PROMISE = {
    "subject": "Online orders for {bakery_name}",
    "body": (
        "Hi {owner_first_name},\n\n"
        "{bakery_name} has {review_count} reviews and a line out the door. "
        "Online ordering is guaranteed to shorten it.\n\n"
        "See https://ellis-web-studio.example/examples and "
        "https://ellis-web-studio.example/pricing\n\n"
        "Would a quick example help?\n\n"
        "Jordan Ellis"
    ),
}

# ONE ENTRY PER BAKERY THE QUALIFY STAGE HANDS OVER, IN HANDOVER ORDER: the
# nine qualified bakeries in listing order, then the four chased ones.
#
#   first_draft    "template" fills MESSAGE_TEMPLATES. The two other values name
#                  one of the deliberately bad drafts above.
#   designed_send  what the offline sender reports when this bakery goes out on
#                  this channel. None for a held draft: it is never sent. An
#                  email that is not delivered is a bounce. An Instagram message
#                  that is not delivered is one nobody opened.
BAKERY_OUTREACH_ROWS: tuple[dict[str, Any], ...] = (
    {
        "listing_row": 1,
        "lead_id": "apify-maps-72a168d9e318",
        "place_id": "fixture-place-001",
        "bakery_name": "Sunrise Bakery",
        "how_orders_work_today": "the menu on your site still asks people to call in cake orders",
        "first_draft": "template",
        "designed_send": {"channel": "email", "delivered": True, "replied": True},
    },
    {
        "listing_row": 2,
        "lead_id": "apify-maps-f0fe374ccec2",
        "place_id": "fixture-place-002",
        "bakery_name": "Riverbend Bread",
        "how_orders_work_today": "your site lists a phone number for orders and nothing else",
        "first_draft": "template",
        "designed_send": {"channel": "email", "delivered": False, "replied": False},
    },
    {
        "listing_row": 4,
        "lead_id": "apify-maps-9fd995a9cb11",
        "place_id": "fixture-place-004",
        "bakery_name": "Flour & Hearth",
        "how_orders_work_today": "orders come in through Instagram messages and the phone",
        "first_draft": "template",
        "designed_send": {"channel": "email", "delivered": False, "replied": False},
    },
    {
        # Qualifies because Hunter, second in Enrich's waterfall, finds the
        # owner. Apollo only had a baker on staff.
        "listing_row": 5,
        "lead_id": "apify-maps-b1376a14177b",
        "place_id": "fixture-place-005",
        "bakery_name": "Morning Crumb",
        "how_orders_work_today": "orders come in by phone and at the counter",
        "first_draft": "template",
        "designed_send": {"channel": "email", "delivered": True, "replied": False},
    },
    {
        "listing_row": 6,
        "lead_id": "apify-maps-cb488d4932cd",
        "place_id": "fixture-place-006",
        "bakery_name": "Bluebird Bakery",
        "how_orders_work_today": "your site says to call or walk in to order",
        "first_draft": "template",
        "designed_send": {"channel": "email", "delivered": True, "replied": False},
    },
    {
        "listing_row": 9,
        "lead_id": "apify-maps-de70049ff199",
        "place_id": "fixture-place-009",
        "bakery_name": "Lantern Lane Pies",
        "how_orders_work_today": "holiday pre-orders still run on a paper form",
        "first_draft": "template",
        "designed_send": {"channel": "email", "delivered": True, "replied": False},
    },
    {
        "listing_row": 12,
        "lead_id": "apify-maps-5cfae4b54a99",
        "place_id": "fixture-place-012",
        "bakery_name": "Sweet Thistle Bakeshop",
        "how_orders_work_today": "custom cakes are ordered through an email thread",
        "first_draft": "template",
        "designed_send": {"channel": "email", "delivered": False, "replied": False},
    },
    {
        "listing_row": 19,
        "lead_id": "apify-maps-423d3ade609c",
        "place_id": "fixture-place-019",
        "bakery_name": "Honeycomb Oven",
        "how_orders_work_today": "you take orders through Instagram messages",
        "first_draft": "template",
        "designed_send": {"channel": "email", "delivered": True, "replied": True},
    },
    {
        "listing_row": 25,
        "lead_id": "apify-maps-08a0b0bfedca",
        "place_id": "fixture-place-025",
        "bakery_name": "Night Owl Bagels & Bakes",
        "how_orders_work_today": "group bagel orders come in by phone",
        "first_draft": "shouting_spam",
        "designed_send": None,
    },
    {
        "listing_row": 11,
        "lead_id": "apify-maps-62e64e5b88ed",
        "place_id": "fixture-place-011",
        "bakery_name": "Butter & Thyme Bakery",
        "how_orders_work_today": "orders at all four shops come in by phone",
        "first_draft": "template",
        "designed_send": {"channel": "email_campaign_2", "delivered": True, "replied": False},
    },
    {
        "listing_row": 13,
        "lead_id": "apify-maps-e37b7bfc1eb6",
        "place_id": "fixture-place-013",
        "bakery_name": "Tiny Kiln Bakery",
        "how_orders_work_today": "you take orders at the counter",
        "first_draft": "template",
        "designed_send": {"channel": "email_campaign_2", "delivered": True, "replied": False},
    },
    {
        "listing_row": 21,
        "lead_id": "apify-maps-a29a96a58a53",
        "place_id": "fixture-place-021",
        "bakery_name": "Grand Avenue Cake Hall",
        "how_orders_work_today": "the line for cakes runs out the door",
        "first_draft": "polite_with_two_links_and_a_promise",
        "designed_send": None,
    },
    {
        "listing_row": 24,
        "lead_id": "apify-maps-d4321a585a22",
        "place_id": "fixture-place-024",
        "bakery_name": "Porchlight Pastry",
        "how_orders_work_today": "you sell at the weekend market and take orders by message",
        "first_draft": "template",
        "designed_send": {"channel": "email_campaign_2", "delivered": True, "replied": False},
    },
)

OUTREACH_ROW_BY_LEAD_ID = {row["lead_id"]: row for row in BAKERY_OUTREACH_ROWS}
OUTREACH_ROW_BY_PLACE_ID = {row["place_id"]: row for row in BAKERY_OUTREACH_ROWS}

# The observation for every other bakery on the list, keyed by place id. The
# default run never writes to these ten: qualify lets them go or parks them
# for review. They are here so a run with changed rules (a wider band, a cleared
# review) still writes a specific sentence and not the stand-in above.
HOW_ORDERS_WORK_TODAY_FOR_BAKERIES_NOT_HANDED_OVER: dict[str, str] = {
    # row 3: Oak Street Bakes
    "fixture-place-003":
        "your site already has an order button and a cart",
    # row 8: Golden Wheat Bakery Cafe
    "fixture-place-008":
        "each of your cafes takes orders at the counter",
    # row 10: Velvet Whisk Patisserie
    "fixture-place-010":
        "your home page links to a delivery app for orders",
    # row 14: Fig & Feather Bakery
    "fixture-place-014":
        "orders come in by phone and at the counter",
    # row 15: Tin Roof Bakery
    "fixture-place-015":
        "your site does not say how to place an order",
    # row 17: Poppyseed Bakehouse
    "fixture-place-017":
        "your site already has a shop page with a cart",
    # row 18: Rye & Rosemary Bakery
    "fixture-place-018":
        "the Order Ahead button on your site leads to a phone number and an emailed form",
    # row 20: Daily Dozen Donuts & Bakery
    "fixture-place-020":
        "orders go through your own ordering app",
    # row 22: Little Anvil Bread Co.
    "fixture-place-022":
        "pre-orders go through an ordering app linked from your site",
    # row 23: Dos Rios Panaderia
    "fixture-place-023":
        "your site does not say how to place an order",
}

# Rye & Rosemary Bakery is parked for review in the default run. If a person
# checks its order page and clears it, it joins the Instagram lane, and this is
# what the offline sender reports: opened, no reply.
DESIGNED_SEND_ONCE_A_REVIEW_IS_CLEARED: dict[str, dict[str, Any]] = {
    "fixture-place-018": {"channel": "email", "delivered": True, "replied": False},
}


def outreach_row_for(lead: dict[str, Any]) -> dict[str, Any]:
    """This bakery's row above. Lead id first, then the Google Maps place id."""
    place_id = (lead.get("source") or {}).get("place_id", "")
    return (OUTREACH_ROW_BY_LEAD_ID.get(lead.get("id", ""))
            or OUTREACH_ROW_BY_PLACE_ID.get(place_id) or {})


def how_orders_work_today_for(lead: dict[str, Any]) -> str:
    """The one specific, checkable observation the message makes about a bakery."""
    place_id = (lead.get("source") or {}).get("place_id", "")
    return (outreach_row_for(lead).get("how_orders_work_today")
            or HOW_ORDERS_WORK_TODAY_FOR_BAKERIES_NOT_HANDED_OVER.get(place_id)
            or DEFAULT_HOW_ORDERS_WORK_TODAY)

DELIBERATELY_BAD_DRAFTS = {
    "shouting_spam": SHOUTING_SPAM_DRAFT,
    "polite_with_two_links_and_a_promise": POLITE_DRAFT_WITH_TWO_LINKS_AND_A_PROMISE,
}


def render_message(template: dict[str, str], lead: dict[str, Any]) -> dict[str, str]:
    """Fill one template's four placeholders from one lead."""
    values = outreach_placeholder_values(lead, how_orders_work_today_for(lead))
    return {"subject": template["subject"].format(**values),
            "body": template["body"].format(**values)}


class BakeryTemplateCopyProvider:
    """The offline writer. It obeys the rules the prompt asks Claude to obey.

    It is deliberately GOOD copy, not placeholder text: the lint in outreach.py
    has to be exercised against realistic input, and a learner reading the
    offline output should see the shape they are aiming for. Two bakeries get a
    deliberately bad first draft instead, so the lint is seen to work.
    """

    live = False
    name = "claude(mock)"

    def write(self, lead: dict[str, Any], offer: str) -> dict[str, str]:
        """The first draft, in email form. This is what the lint reads."""
        first_draft = outreach_row_for(lead).get("first_draft", "template")
        bad_draft = DELIBERATELY_BAD_DRAFTS.get(first_draft)
        return render_message(bad_draft or MESSAGE_TEMPLATES[FIRST_DRAFT_CHANNEL], lead)

    def write_for_channel(self, lead: dict[str, Any], offer: str,
                          channel: str) -> dict[str, str]:
        """The same message, re-worded for the channel it was allocated to."""
        template = MESSAGE_TEMPLATES.get(channel, MESSAGE_TEMPLATES[FIRST_DRAFT_CHANNEL])
        return render_message(template, lead)


def repeatable_draw(lead_id: str, purpose: str) -> float:
    """A 0..1 number that is the same on every machine for the same inputs."""
    digest = hashlib.sha256(f"{purpose}:{lead_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


class BakeryFixtureSendProvider:
    """The offline sender. NOTHING LEAVES THIS MACHINE.

    IT RETURNS OUTCOMES, NOT SUCCESS. `sent` is not a result. A real sender
    reports bounces and replies later, through a webhook, and a pipeline that
    treats "accepted for delivery" as "delivered" has no idea what its funnel
    looks like. So this sender gives the same three-part outcome a real one
    eventually gives you: delivered, bounced, replied.

    A bakery going out on its designed channel gets its designed outcome. Any
    other pairing (a second round that moved it to another lane, or a bakery
    from your own search) gets a repeatable draw against CHANNEL_MODEL.

    A CHASED LEAD NEVER REPLIES, designed or drawn. It was outside the rules
    when it was sent, so it costs a message and converts nothing. run_outreach
    tells this sender which leads were chased through `chased=True`.
    """

    live = False
    name = "sender(mock)"
    accepts_chased_flag = True

    def send(self, lead: dict[str, Any], channel: str,
             message: dict[str, str], *, chased: bool = False) -> dict[str, Any]:
        place_id = (lead.get("source") or {}).get("place_id", "")
        designed = (outreach_row_for(lead).get("designed_send")
                    or DESIGNED_SEND_ONCE_A_REVIEW_IS_CLEARED.get(place_id))
        if designed and designed["channel"] == channel:
            delivered, replied = designed["delivered"], designed["replied"]
        else:
            rates = CHANNEL_MODEL.get(channel, CHANNEL_MODEL["email"])
            delivered = repeatable_draw(lead["id"], f"deliver:{channel}") < rates["deliver"]
            replied = delivered and (
                repeatable_draw(lead["id"], f"convert:{channel}") < rates["convert"])
        replied = replied and not chased
        return {
            "lead_id": lead["id"],
            "channel": channel,
            "subject": message["subject"],
            "sent": True,
            "delivered": delivered,
            # A non-delivery on email is a bounce, a real event that damages
            # the sending address. On Instagram it is simply a message nobody
            # opened. Naming both "bounced" would overstate the damage.
            "bounced": (not delivered) and channel.startswith("email"),
            "replied": replied,
        }
