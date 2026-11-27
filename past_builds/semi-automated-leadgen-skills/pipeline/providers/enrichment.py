"""enrichment.py — the contact-data services (Apollo, Hunter) and the research agent.

Enrich uses three tools, in this order:

  1. APOLLO, a contact-data service. You give it the bakery from Source. It
     looks up owner_name, owner_role, email and location_count. It is also
     asked for ordering_platform, and it knows that for only 3 of 23 bakeries.
     Contact databases track people and companies, not how a bakery takes
     orders.

  2. HUNTER (hunter.io), an email-finder service. It searches a company's web
     domain for the people who work there and their emails. It costs more per
     lookup than Apollo, so it is asked second, and only for the contact
     fields Apollo left empty. It can find owner_name, owner_role and email,
     never location_count or ordering_platform.

  3. THE RESEARCH AGENT, for each bakery neither service could answer about
     ordering_platform. It reads the bakery's website, finds the order page,
     and returns "no online ordering" or the kind of platform, with the page it
     read as the source. It fills that one field only. The agent itself is
     built in Repository 2 (research-agent-four-ways). This repo replays its
     recorded answers from fixtures/research_agent_answers.json, so it runs
     offline and free.

Apollo and Hunter together are the CONTACT WATERFALL: an ordered list of
services, cheapest first. get_contact_data_providers() returns that list.

An agent answer is not written into the record here. Enrich copies it in only
after you confirm it (see enrich.py and review_research.py).

COST. The fixture uses simulated per-field cost units for teaching. Live Apollo
and Hunter charges depend on account terms and returned data; the course
ledger is not an invoice.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Iterable

from .bakery_enrichment_fixture import BAKERY_ENRICHMENT_ROWS
from .base import ProviderError, announce, env_key

log = logging.getLogger("pipeline.providers")

# The fields Apollo is asked for. Kept in sync with schema.ENRICHED_FIELDS.
APOLLO_FIELDS = ("owner_name", "owner_role", "email", "location_count",
                 "ordering_platform")

# The fields Hunter can find. It finds people and their emails, nothing else.
HUNTER_FIELDS = ("owner_name", "owner_role", "email")

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_AGENT_ANSWERS = REPO_ROOT / "fixtures" / "research_agent_answers.json"

# The same table, indexed two ways. The lead id is the normal route. The place
# id is the fallback for a fork that numbers its leads differently.
FIXTURE_ROW_BY_LEAD_ID = {row["lead_id"]: row for row in BAKERY_ENRICHMENT_ROWS}
FIXTURE_ROW_BY_PLACE_ID = {row["place_id"]: row for row in BAKERY_ENRICHMENT_ROWS}

_warned_unknown_lead_ids: set[str] = set()


def fixture_row_for(lead: dict[str, Any]) -> dict[str, Any] | None:
    """Find this lead's row in the offline table, or None when it has none."""
    row = FIXTURE_ROW_BY_LEAD_ID.get(lead.get("id"))
    if row is None:
        place_id = (lead.get("source") or {}).get("place_id")
        row = FIXTURE_ROW_BY_PLACE_ID.get(place_id)
    return row


class MockApolloProvider:
    """Offline Apollo: answers from bakery_enrichment_fixture.py."""

    live = False
    name = "apollo(mock)"
    fields_it_can_find = APOLLO_FIELDS
    cost_per_hit = 0.004     # about $4 per 1,000 fields found
    cost_per_miss = 0.001    # a lookup that finds nothing still uses a credit

    def lookup(self, lead: dict[str, Any], fields: Iterable[str]) -> dict[str, Any]:
        """Return the fields Apollo has for this lead. A missing key is a miss."""
        row = fixture_row_for(lead)
        if row is None:
            lead_id = str(lead.get("id"))
            if lead_id not in _warned_unknown_lead_ids:
                _warned_unknown_lead_ids.add(lead_id)
                log.warning("offline Apollo has no record of %s (%s). Set "
                            "APOLLO_API_KEY to look up your own list.",
                            lead.get("name"), lead_id)
            return {}
        return {field: row["apollo"][field] for field in fields
                if row["apollo"].get(field) is not None}


class HttpApolloProvider:
    """Read a current decision maker using Apollo's search and match endpoints.

    Search does not return email. Match can consume account credits. Missing
    data stays missing: an office address is not evidence of one retail shop,
    and technology detection is not proof about the ordering checkout.
    """
    live = True
    name = "apollo"
    fields_it_can_find = APOLLO_FIELDS
    # Course cost units only; these are NOT Apollo's live invoice prices.
    cost_per_hit = 0.004
    cost_per_miss = 0.001
    api_root = "https://api.apollo.io/api/v1"

    def __init__(self, key: str) -> None:
        self.key = key

    def _request(self, route: str, params: dict) -> dict:
        from urllib.request import Request, urlopen
        from urllib.parse import urlencode
        from urllib.error import HTTPError, URLError
        request = Request(self.api_root + route + '?' + urlencode(params, doseq=True),
                          data=b'', method='POST',
                          headers={'x-api-key': self.key, 'Accept': 'application/json'})
        try:
            with urlopen(request, timeout=30) as response:
                payload = json.load(response)
        except HTTPError as error:
            raise ProviderError(f'Apollo returned HTTP {error.code}. Check account access, endpoint permissions and available credits.') from None
        except (URLError, TimeoutError, ValueError) as error:
            raise ProviderError('Apollo request failed or returned unreadable data; no facts were added.') from None
        if not isinstance(payload, dict):
            raise ProviderError('Apollo returned an unexpected response shape.')
        return payload

    @staticmethod
    def _domain(value: str) -> str:
        from urllib.parse import urlparse
        host = urlparse(value if '://' in value else 'https://' + value).hostname or ''
        return host.lower().removeprefix('www.')

    @staticmethod
    def _role(person: dict) -> str | None:
        seniority = str(person.get('seniority') or '').lower()
        if seniority in ('owner', 'founder'):
            return 'Owner'
        title = str(person.get('title') or '').strip().lower()
        if title in ('owner', 'co-owner', 'co owner', 'founder', 'co-founder', 'founder and owner', 'owner/operator', 'owner & operator'):
            return 'Owner'
        if title in ('general manager', 'bakery manager', 'store manager', 'manager'):
            return 'Manager'
        # Do not treat a product owner, marketing manager or unknown title as
        # the bakery owner merely because its text contains a matching word.
        return None

    def lookup(self, lead: dict[str, Any], fields: Iterable[str]) -> dict[str, Any]:
        domain = self._domain(str(lead.get('website') or ''))
        if not domain or domain.endswith('.example'):
            return {}  # Fictional fixtures must never be looked up live.
        search = self._request('/mixed_people/api_search', {
            'q_organization_domains_list[]': [domain],
            'person_seniorities[]': ['owner', 'founder', 'manager'],
            'person_titles[]': ['owner', 'founder', 'general manager', 'bakery manager', 'store manager'],
            'include_similar_titles': 'false', 'page': 1, 'per_page': 5,
        })
        candidates = search.get('people') or []
        if not candidates:
            return {}
        candidates.sort(key=lambda p: 0 if self._role(p) == 'Owner' else 1)
        person_id = candidates[0].get('id') or candidates[0].get('person_id')
        if not person_id:
            return {}
        match = self._request('/people/match', {
            'id': person_id, 'domain': domain,
            'reveal_personal_emails': 'false', 'reveal_phone_number': 'false',
            'run_waterfall_email': 'false', 'run_waterfall_phone': 'false',
        })
        person = match.get('person') or {}
        organization = person.get('organization') or {}
        current_domain = self._domain(str(organization.get('primary_domain') or organization.get('website_url') or ''))
        if current_domain != domain or match.get('match_confidence') in ('low', 'none'):
            return {}  # Search may match a previous employer; verify the current one.
        found = {}
        role = self._role(person)
        if role:
            found['owner_role'] = role
            if person.get('name'):
                found['owner_name'] = person['name']
            email = person.get('email')
            if isinstance(email, str) and '@' in email and person.get('email_status') == 'verified':
                found['email'] = email
        # location_count and ordering_platform require business evidence; never
        # infer either from headcount, a headquarters address or missing tech.
        return {field: value for field, value in found.items() if field in fields}

def get_contact_data_provider(*, apollo_key: str | None = None) -> Any:
    """Return Apollo: live when APOLLO_API_KEY is set, offline otherwise."""
    key_name, key = ("APOLLO_API_KEY", apollo_key) if apollo_key else env_key("APOLLO_API_KEY")
    announce("apollo", "APOLLO_API_KEY", bool(key))
    return HttpApolloProvider(key) if key else MockApolloProvider()


class MockHunterProvider:
    """Offline Hunter: answers from the `hunter` entries in bakery_enrichment_fixture.py."""

    live = False
    name = "hunter(mock)"
    fields_it_can_find = HUNTER_FIELDS
    cost_per_hit = 0.010     # dearer than Apollo: about $10 per 1,000 fields found
    cost_per_miss = 0.003    # a search that finds nothing still uses a credit

    def lookup(self, lead: dict[str, Any], fields: Iterable[str]) -> dict[str, Any]:
        """Return the fields Hunter has for this lead. A missing key is a miss."""
        row = fixture_row_for(lead)
        if row is None:
            return {}  # Apollo has already warned about a bakery the fixture lacks.
        hunter = row.get("hunter") or {}
        return {field: hunter[field] for field in fields
                if hunter.get(field) is not None}


class HttpHunterProvider:
    """Find a decision maker and their email with Hunter's domain search.

    Domain search lists the people Hunter has found on one company's web
    domain. Only an owner or a manager is kept, and only a verified email.
    Hunter never says how many shops a bakery has or how it takes orders.
    A bakery with no website has no domain to search, so it is a miss.
    """
    live = True
    name = "hunter"
    fields_it_can_find = HUNTER_FIELDS
    # Course cost units only; these are NOT Hunter's live invoice prices.
    cost_per_hit = 0.010
    cost_per_miss = 0.003
    api_root = "https://api.hunter.io/v2"

    def __init__(self, key: str) -> None:
        self.key = key

    def _request(self, route: str, params: dict) -> dict:
        from urllib.request import Request, urlopen
        from urllib.parse import urlencode
        from urllib.error import HTTPError, URLError
        request = Request(self.api_root + route + '?' + urlencode(params),
                          method='GET',
                          headers={'X-API-KEY': self.key, 'Accept': 'application/json'})
        try:
            with urlopen(request, timeout=30) as response:
                payload = json.load(response)
        except HTTPError as error:
            raise ProviderError(f'Hunter returned HTTP {error.code}. Check the key, the plan and the remaining searches.') from None
        except (URLError, TimeoutError, ValueError) as error:
            raise ProviderError('Hunter request failed or returned unreadable data; no facts were added.') from None
        if not isinstance(payload, dict):
            raise ProviderError('Hunter returned an unexpected response shape.')
        return payload

    @staticmethod
    def _role(person: dict) -> str | None:
        position = str(person.get('position') or '').strip().lower()
        if position in ('owner', 'co-owner', 'co owner', 'founder', 'co-founder', 'founder and owner', 'owner/operator', 'owner & operator'):
            return 'Owner'
        if position in ('general manager', 'bakery manager', 'store manager', 'manager'):
            return 'Manager'
        # A marketing manager or an unknown title is not the bakery owner.
        return None

    def lookup(self, lead: dict[str, Any], fields: Iterable[str]) -> dict[str, Any]:
        domain = HttpApolloProvider._domain(str(lead.get('website') or ''))
        if not domain or domain.endswith('.example'):
            return {}  # Fictional fixtures must never be looked up live.
        search = self._request('/domain-search', {
            'domain': domain, 'type': 'personal', 'limit': 10,
        })
        people = (search.get('data') or {}).get('emails') or []
        decision_makers = [person for person in people if self._role(person)]
        if not decision_makers:
            return {}
        decision_makers.sort(key=lambda person: 0 if self._role(person) == 'Owner' else 1)
        person = decision_makers[0]
        found = {'owner_role': self._role(person)}
        full_name = ' '.join(part for part in (person.get('first_name'), person.get('last_name')) if part)
        if full_name:
            found['owner_name'] = full_name
        email = person.get('value')
        if isinstance(email, str) and '@' in email and (person.get('verification') or {}).get('status') == 'valid':
            found['email'] = email
        return {field: value for field, value in found.items() if field in fields}


def get_email_finder_provider(*, hunter_key: str | None = None) -> Any:
    """Return Hunter: live when HUNTER_API_KEY is set, offline otherwise."""
    key_name, key = ("HUNTER_API_KEY", hunter_key) if hunter_key else env_key("HUNTER_API_KEY")
    announce("hunter", "HUNTER_API_KEY", bool(key))
    return HttpHunterProvider(key) if key else MockHunterProvider()


def get_contact_data_providers(*, apollo_key: str | None = None,
                               hunter_key: str | None = None) -> list[Any]:
    """Return the contact waterfall, cheapest first: [Apollo, Hunter]."""
    return [get_contact_data_provider(apollo_key=apollo_key),
            get_email_finder_provider(hunter_key=hunter_key)]


class RecordedResearchAgent:
    """Replays the research agent's recorded answers. No network, no cost.

    Each answer has Repository 2's verdict shape:
      verdict.supportsNoOnlineOrdering   True: the pages show no online ordering
      verdict.researchReason             one sentence
      verdict.sourceUrls                 the pages the verdict rests on
    plus two keys this repo needs:
      platform_kind   the kind of online ordering, when the verdict is False
                      and a page shows one; None when the agent could not tell
      evidence        {source_url, quote}: the page and passage you check
    """

    live = False
    name = "research_agent(recorded)"
    # What one live run of the agent costs, roughly: several model calls plus
    # page reads. Billed per run, whatever the answer.
    cost_per_run = 0.09

    def __init__(self, answers_path: Path | str = DEFAULT_AGENT_ANSWERS) -> None:
        payload = json.loads(Path(answers_path).read_text())
        self.answers = {answer["lead_id"]: answer for answer in payload["answers"]}
        log.info("provider %-10s RECORDED (Repository 2's agent, %s)",
                 "research", Path(answers_path).name)

    def research_ordering_platform(self, lead: dict[str, Any]) -> dict[str, Any] | None:
        """Return the recorded answer for this bakery, or None if there is none."""
        return self.answers.get(lead.get("id"))


def ordering_platform_from_answer(answer: dict[str, Any] | None) -> str | None:
    """Map one agent answer to an ordering_platform value, or None if unclear.

    supportsNoOnlineOrdering True          -> "none"
    False, with a platform kind named      -> that kind
    False, no platform kind (unproven)     -> None: the agent could not tell
    """
    if not answer:
        return None
    if answer["verdict"]["supportsNoOnlineOrdering"] is True:
        return "none"
    return answer.get("platform_kind") or None
