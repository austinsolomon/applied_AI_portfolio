"""Free contact extraction: phone numbers and emails already written on cached page text.

Exa sourcing returns each business's homepage text and caches it. Most small businesses print a
phone or an email on that page, so reading it costs nothing and gives Exa leads the same dedupe
keys Maps leads carry. Paid enrichment (BlitzAPI) then runs only for leads still missing both.
"""

import re

from leadgen.contacts import normalize_domain
from leadgen.crm_write import clean_email, clean_phone

# A US number: optional +1, area code and exchange starting 2-9, written with spaces, dots,
# dashes or brackets. The look-arounds keep it from matching inside longer digit runs.
US_PHONE = re.compile(
    r"(?<![\d.])(?:\+?1[\s.\-]?)?\(?([2-9]\d{2})\)?[\s.\-]?([2-9]\d{2})[\s.\-]?(\d{4})(?![\d.])"
)
EMAIL_IN_TEXT = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")


def extract_phone(text: str) -> str | None:
    for match in US_PHONE.finditer(text or ""):
        written = match.group(0)
        # Eleven bare digits ("12345678901") are an order or ID number, not "+1" and a phone.
        if len(re.sub(r"\D", "", written)) == 11 and written.isdigit():
            continue
        area, exchange, line = match.groups()
        phone = clean_phone(f"({area}) {exchange}-{line}")
        if phone:
            return phone
    return None


def extract_email(text: str, website: str | None = None) -> str | None:
    """The first real email, preferring one on the business's own domain."""
    found = [clean_email(match.rstrip(".")) for match in EMAIL_IN_TEXT.findall(text or "")]
    emails = list(dict.fromkeys(email.lower() for email in found if email))
    domain = normalize_domain(website)
    own = [email for email in emails if domain and email.split("@")[1].endswith(domain)]
    return (own or emails or [None])[0]
