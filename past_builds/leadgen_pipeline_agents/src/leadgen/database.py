"""Database connections."""

import psycopg

from leadgen.settings import get_settings


def connect(database_url: str | None = None) -> psycopg.Connection:
    """Open an autocommit connection to `database_url`, or to the configured DATABASE_URL.

    Autocommit means nothing is wrapped in a transaction implicitly; code that needs one opens it
    explicitly with `connection.transaction()`.
    """
    return psycopg.connect(database_url or str(get_settings().database_url), autocommit=True)
