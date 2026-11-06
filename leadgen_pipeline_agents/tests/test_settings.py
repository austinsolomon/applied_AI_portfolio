"""Typed configuration fails at startup, with a clear cause, when DATABASE_URL is wrong."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from leadgen.settings import Settings


@pytest.fixture
def no_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run from an empty directory so a developer's .env cannot leak into the test."""
    monkeypatch.chdir(tmp_path)


@pytest.mark.usefixtures("no_env_file")
def test_missing_database_url_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(ValidationError, match="database_url"):
        Settings()


@pytest.mark.usefixtures("no_env_file")
@pytest.mark.parametrize("bad_url", ["not a url", "mysql://user@host/db"])
def test_non_postgres_database_url_is_an_error(
    monkeypatch: pytest.MonkeyPatch, bad_url: str
) -> None:
    monkeypatch.setenv("DATABASE_URL", bad_url)

    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.usefixtures("no_env_file")
def test_postgres_database_url_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:password@localhost:5433/leadgen")

    settings = Settings()

    assert settings.database_url.hosts()[0]["port"] == 5433
