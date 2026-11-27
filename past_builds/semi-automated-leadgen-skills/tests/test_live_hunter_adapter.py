import json
from io import BytesIO
from urllib.parse import urlparse, parse_qs
import pytest
from pipeline.providers.enrichment import HttpHunterProvider


def person(**overrides):
    return {'value': 'owner@bakery.test', 'type': 'personal', 'first_name': 'A', 'last_name': 'Baker', 'position': 'Owner', 'verification': {'status': 'valid'}, **overrides}


def test_live_adapter_searches_the_domain_and_only_maps_proven_fields(monkeypatch):
    calls = []
    def urlopen(request, timeout):
        calls.append(request)
        return BytesIO(json.dumps({'data': {'emails': [person()]}}).encode())
    monkeypatch.setattr('urllib.request.urlopen', urlopen)
    fields = ['owner_name', 'owner_role', 'email']
    result = HttpHunterProvider('test-key').lookup({'website': 'https://www.bakery.test'}, fields)
    assert result == {'owner_name': 'A Baker', 'owner_role': 'Owner', 'email': 'owner@bakery.test'}
    assert urlparse(calls[0].full_url).path == '/v2/domain-search'
    assert parse_qs(urlparse(calls[0].full_url).query)['domain'] == ['bakery.test']
    assert 'api_key' not in parse_qs(urlparse(calls[0].full_url).query), 'the key goes in a header, not the URL'


def test_live_adapter_returns_only_the_fields_it_was_asked_for(monkeypatch):
    provider = HttpHunterProvider('test-key')
    monkeypatch.setattr(provider, '_request', lambda *args: {'data': {'emails': [person()]}})
    assert provider.lookup({'website': 'https://bakery.test'}, ['email']) == {'email': 'owner@bakery.test'}


@pytest.mark.parametrize('found', [person(position='Marketing Manager'), person(verification={'status': 'accept_all'})])
def test_live_adapter_never_guesses_a_role_or_uses_an_unverified_email(monkeypatch, found):
    provider = HttpHunterProvider('test-key')
    monkeypatch.setattr(provider, '_request', lambda *args: {'data': {'emails': [found]}})
    result = provider.lookup({'website': 'https://bakery.test'}, ['owner_name', 'owner_role', 'email'])
    assert 'email' not in result


def test_fictional_or_missing_website_makes_no_request(monkeypatch):
    provider = HttpHunterProvider('test-key')
    monkeypatch.setattr(provider, '_request', lambda *args: pytest.fail('fictional site must not call Hunter'))
    assert provider.lookup({'website': 'https://tin-roof-bakery.example'}, ['email']) == {}
    assert provider.lookup({'website': ''}, ['email']) == {}
