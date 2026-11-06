"""The Data model tab's guide covers every table and view, and its examples match real columns."""

import psycopg

from leadgen.data_model import all_guides


def schema_objects(connection: psycopg.Connection) -> dict[str, set[str]]:
    rows = connection.execute(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = 'public'"
    ).fetchall()
    columns: dict[str, set[str]] = {}
    for table, column in rows:
        columns.setdefault(table, set()).add(column)
    return columns


def test_every_table_and_view_has_a_guide_entry(migrated_connection: psycopg.Connection) -> None:
    guided = {guide.name for guide in all_guides()}
    assert set(schema_objects(migrated_connection)) == guided


def test_example_rows_only_use_real_columns_and_say_one_thing_each(
    migrated_connection: psycopg.Connection,
) -> None:
    columns = schema_objects(migrated_connection)
    for guide in all_guides():
        assert guide.examples, f"{guide.name} has no example rows"
        assert guide.summary.endswith(".") and guide.summary.count(". ") == 0, guide.name
        for row in guide.examples:
            assert set(row) <= columns[guide.name], (guide.name, set(row) - columns[guide.name])
