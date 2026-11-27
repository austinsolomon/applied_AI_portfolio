import socket

import pytest

from leadgen.public_urls import validate_public_url


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]",
        "http://localhost/",
        "https://host.internal",
        "file:///etc/passwd",
        "https://user:password@8.8.8.8",
        "http://8.8.8.8:5432",
    ],
)
def test_internal_evidence_urls_are_rejected(url):
    with pytest.raises(ValueError):
        validate_public_url(url)


def test_dns_resolving_to_private_address_is_rejected(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("10.0.0.1", 443))])
    with pytest.raises(ValueError, match="non-public"):
        validate_public_url("https://apparently-public.example")


def test_all_dns_answers_must_be_public(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("8.8.8.8", 443)), (2, 1, 6, "", ("10.0.0.1", 443))],
    )
    with pytest.raises(ValueError, match="non-public"):
        validate_public_url("https://mixed.example")


def test_public_url_passes_without_restricting_business_paths():
    validate_public_url("https://8.8.8.8/services?city=Phoenix")
