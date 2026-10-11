"""Apply numbered SQL migration files in order, each exactly once.

Files are named `NNNN_description.sql` and live in `leadgen/migrations/`. Each file runs in its own
transaction together with its row in `schema_migrations`, so a file that fails leaves no trace.
A file that has been applied must never change afterwards: its checksum is stored and compared on
every run. An advisory lock stops two processes from migrating the same database at once.
"""

import hashlib
import re
from dataclasses import dataclass
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path

import psycopg

MIGRATION_FILENAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")
PACKAGED_MIGRATIONS = files("leadgen") / "migrations"
MIGRATION_LOCK_ID = 7_265_311  # any constant shared by every process that migrates this database

CREATE_TRACKING_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    integer     PRIMARY KEY,
    name       text        NOT NULL,
    checksum   text        NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


class MigrationError(Exception):
    """The migration files or the database's migration history are inconsistent."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str
    checksum: str


def discover_migrations(directory: Path | Traversable = PACKAGED_MIGRATIONS) -> list[Migration]:
    """Read every `.sql` file in `directory`, ordered by version number."""
    migrations = []
    for entry in directory.iterdir():
        if not entry.name.endswith(".sql"):
            continue
        filename_match = MIGRATION_FILENAME.match(entry.name)
        if filename_match is None:
            raise MigrationError(
                f"{entry.name}: migration files must be named NNNN_description.sql"
            )
        sql = entry.read_text(encoding="utf-8")
        checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        migrations.append(Migration(int(filename_match.group(1)), entry.name, sql, checksum))

    migrations.sort(key=lambda migration: migration.version)
    versions = [migration.version for migration in migrations]
    if len(versions) != len(set(versions)):
        raise MigrationError("two migration files share the same version number")
    return migrations


def apply_migrations(
    connection: psycopg.Connection, migrations: list[Migration]
) -> list[Migration]:
    """Apply the migrations the database has not seen yet. Returns the ones applied now."""
    connection.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_ID,))
    try:
        connection.execute(CREATE_TRACKING_TABLE)
        applied_checksums = dict(
            connection.execute("SELECT version, checksum FROM schema_migrations").fetchall()
        )
        _check_history(migrations, applied_checksums)

        newly_applied = []
        for migration in migrations:
            if migration.version in applied_checksums:
                continue
            with connection.transaction():
                connection.execute(migration.sql)
                connection.execute(
                    "INSERT INTO schema_migrations (version, name, checksum) VALUES (%s, %s, %s)",
                    (migration.version, migration.name, migration.checksum),
                )
            newly_applied.append(migration)
        return newly_applied
    finally:
        connection.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_ID,))


def _check_history(migrations: list[Migration], applied_checksums: dict[int, str]) -> None:
    """Refuse to run if applied files were edited or the database is ahead of the code."""
    known_versions = {migration.version for migration in migrations}
    unknown_versions = sorted(set(applied_checksums) - known_versions)
    if unknown_versions:
        raise MigrationError(
            f"the database has migrations this code does not know about: {unknown_versions}"
        )
    for migration in migrations:
        stored_checksum = applied_checksums.get(migration.version)
        if stored_checksum is not None and stored_checksum != migration.checksum:
            raise MigrationError(
                f"{migration.name} was edited after it was applied; add a new migration instead"
            )
