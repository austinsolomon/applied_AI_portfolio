"""The migration runner: order, idempotence, atomicity, and protection of applied history."""

from pathlib import Path

import psycopg
import pytest

from leadgen.migrate import MigrationError, apply_migrations, discover_migrations


def write_migrations(directory: Path, files_by_name: dict[str, str]) -> Path:
    for filename, sql_text in files_by_name.items():
        (directory / filename).write_text(sql_text, encoding="utf-8")
    return directory


def table_exists(connection: psycopg.Connection, table_name: str) -> bool:
    row = connection.execute("SELECT to_regclass(%s) IS NOT NULL", (table_name,)).fetchone()
    return bool(row and row[0])


def recorded_versions(connection: psycopg.Connection) -> list[int]:
    rows = connection.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
    return [version for (version,) in rows]


def test_applies_files_in_version_order(tmp_path: Path, empty_database_url: str) -> None:
    # 0002 depends on the table 0001 creates, so the wrong order would fail.
    write_migrations(
        tmp_path,
        {
            "0002_add_city.sql": "ALTER TABLE markets ADD COLUMN city text;",
            "0001_create_markets.sql": "CREATE TABLE markets (market_id integer PRIMARY KEY);",
        },
    )
    with psycopg.connect(empty_database_url, autocommit=True) as connection:
        applied = apply_migrations(connection, discover_migrations(tmp_path))

        assert [migration.name for migration in applied] == [
            "0001_create_markets.sql",
            "0002_add_city.sql",
        ]
        assert recorded_versions(connection) == [1, 2]


def test_second_run_applies_nothing(tmp_path: Path, empty_database_url: str) -> None:
    write_migrations(tmp_path, {"0001_create_markets.sql": "CREATE TABLE markets (id integer);"})
    migrations = discover_migrations(tmp_path)
    with psycopg.connect(empty_database_url, autocommit=True) as connection:
        apply_migrations(connection, migrations)

        assert apply_migrations(connection, migrations) == []
        assert recorded_versions(connection) == [1]


def test_failing_migration_leaves_no_trace(tmp_path: Path, empty_database_url: str) -> None:
    write_migrations(
        tmp_path,
        {
            "0001_create_markets.sql": "CREATE TABLE markets (id integer);",
            "0002_broken.sql": "CREATE TABLE half_done (id integer); SELECT * FROM no_such_table;",
        },
    )
    with psycopg.connect(empty_database_url, autocommit=True) as connection:
        with pytest.raises(psycopg.errors.UndefinedTable):
            apply_migrations(connection, discover_migrations(tmp_path))

        assert table_exists(connection, "markets")
        assert not table_exists(connection, "half_done")
        assert recorded_versions(connection) == [1]


def test_refuses_an_applied_migration_that_was_edited(
    tmp_path: Path, empty_database_url: str
) -> None:
    migration_path = tmp_path / "0001_create_markets.sql"
    migration_path.write_text("CREATE TABLE markets (id integer);", encoding="utf-8")
    with psycopg.connect(empty_database_url, autocommit=True) as connection:
        apply_migrations(connection, discover_migrations(tmp_path))
        migration_path.write_text("CREATE TABLE markets (id bigint);", encoding="utf-8")

        with pytest.raises(MigrationError, match="edited after it was applied"):
            apply_migrations(connection, discover_migrations(tmp_path))


def test_refuses_a_database_ahead_of_the_code(tmp_path: Path, empty_database_url: str) -> None:
    write_migrations(tmp_path, {"0001_create_markets.sql": "CREATE TABLE markets (id integer);"})
    with psycopg.connect(empty_database_url, autocommit=True) as connection:
        apply_migrations(connection, discover_migrations(tmp_path))
        (tmp_path / "0001_create_markets.sql").unlink()

        with pytest.raises(MigrationError, match="does not know about"):
            apply_migrations(connection, discover_migrations(tmp_path))


@pytest.mark.parametrize(
    ("files_by_name", "message"),
    [
        ({"1_too_short.sql": "SELECT 1;"}, "must be named NNNN_description.sql"),
        ({"0001_Has_Capitals.sql": "SELECT 1;"}, "must be named NNNN_description.sql"),
        ({"0001_first.sql": "SELECT 1;", "0001_second.sql": "SELECT 2;"}, "share the same version"),
    ],
)
def test_rejects_badly_named_or_duplicate_files(
    tmp_path: Path, files_by_name: dict[str, str], message: str
) -> None:
    write_migrations(tmp_path, files_by_name)

    with pytest.raises(MigrationError, match=message):
        discover_migrations(tmp_path)


def test_ignores_files_that_are_not_sql(tmp_path: Path) -> None:
    write_migrations(tmp_path, {"README.md": "# notes", "0001_create.sql": "SELECT 1;"})

    assert [migration.name for migration in discover_migrations(tmp_path)] == ["0001_create.sql"]
