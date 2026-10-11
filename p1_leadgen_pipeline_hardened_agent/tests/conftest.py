"""Shared fixtures. Database tests run against a real Postgres named by DATABASE_URL."""

import uuid
from collections.abc import Iterator
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from psycopg import sql
from pydantic import ValidationError

from leadgen.migrate import apply_migrations, discover_migrations
from leadgen.settings import Settings


@pytest.fixture(scope="session")
def server_database_url() -> str:
    """The configured database, used only to create and drop throwaway test databases."""
    try:
        return str(Settings().database_url)
    except ValidationError:
        pytest.fail(
            "DATABASE_URL is not set. Start Postgres with `docker compose up -d db` and copy "
            ".env.example to .env.",
            pytrace=False,
        )


@pytest.fixture
def empty_database_url(server_database_url: str) -> Iterator[str]:
    """A brand-new, empty database for one test, dropped afterwards."""
    database_name = f"leadgen_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(server_database_url, autocommit=True) as admin_connection:
        admin_connection.execute(
            sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name))
        )
    try:
        # Same server and credentials, different database: swap the URL's path.
        yield urlunsplit(urlsplit(server_database_url)._replace(path=f"/{database_name}"))
    finally:
        with psycopg.connect(server_database_url, autocommit=True) as admin_connection:
            admin_connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database_name))
            )


@pytest.fixture
def migrated_connection(empty_database_url: str) -> Iterator[psycopg.Connection]:
    """A connection to a fresh database with every packaged migration applied."""
    with psycopg.connect(empty_database_url, autocommit=True) as connection:
        apply_migrations(connection, discover_migrations())
        yield connection
