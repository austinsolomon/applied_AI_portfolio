import json
from io import BytesIO
from urllib.parse import urlparse, parse_qs
import pytest
from pipeline.providers.enrichment import HttpApolloProvider
from pipeline.providers.base import ProviderError
from pipeline.review_contact import review_contact


def person(domain='bakery.test', **overrides):
    return {'id': 'person-1', 'name': 'A Baker', 'title': 'Owner', 'email': 'owner@bakery.test', 'email_status': 'verified', 'organization': {'primary_domain': domain}, **overrides}


def test_live_adapter_searches_then_matches_and_only_maps_proven_fields(monkeypatch):
    calls = []
    responses = [{'people': [{'id': 'person-1', 'title': 'Owner'}]}, {'person': person(), 'match_confidence': 'high'}]
    def urlopen(request, timeout):
        calls.append(request)
        return BytesIO(json.dumps(responses.pop(0)).encode())
    monkeypatch.setattr('urllib.request.urlopen', urlopen)
    fields = ['owner_name', 'owner_role', 'email', 'location_count', 'ordering_platform']
    result = HttpApolloProvider('test-key').lookup({'website': 'https://www.bakery.test'}, fields)
    assert result == {'owner_name': 'A Baker', 'owner_role': 'Owner', 'email': 'owner@bakery.test'}
    assert urlparse(calls[0].full_url).path == '/api/v1/mixed_people/api_search'
    assert parse_qs(urlparse(calls[0].full_url).query)['q_organization_domains_list[]'] == ['bakery.test']
    assert urlparse(calls[1].full_url).path == '/api/v1/people/match'
    assert parse_qs(urlparse(calls[1].full_url).query)['reveal_phone_number'] == ['false']


@pytest.mark.parametrize('detail', [person(domain='previous-employer.test'), person(title='Product Owner'), person(email_status='unverified')])
def test_live_adapter_never_guesses_business_facts_or_uses_unverified_email(monkeypatch, detail):
    responses = iter([{'people': [{'id': 'p'}]}, {'person': detail}])
    provider = HttpApolloProvider('test-key')
    monkeypatch.setattr(provider, '_request', lambda *args: next(responses))
    result = provider.lookup({'website': 'https://bakery.test'}, ['owner_name', 'owner_role', 'email', 'location_count'])
    assert 'email' not in result
    assert 'location_count' not in result


def test_fictional_live_input_makes_no_request(monkeypatch):
    provider = HttpApolloProvider('test-key')
    monkeypatch.setattr(provider, '_request', lambda *args: pytest.fail('fictional site must not call Apollo'))
    assert provider.lookup({'website': 'https://sunrise.example'}, ['email']) == {}


def test_manual_shop_count_records_provenance(enriched):
    before = next(r for r in enriched['records'] if r['name'] == 'Rye & Rosemary Bakery')
    result = review_contact(enriched, lead_name=before['name'], field='location_count', value='1', source_url='https://rye-and-rosemary.example/about', reviewed_by='Learner')
    record = next(r for r in result['records'] if r['id'] == before['id'])
    assert record['location_count'] == 1
    assert record['enrichment']['contact_checks'][0]['reviewed_by'] == 'Learner'
    assert record['ordering_platform'] is None
