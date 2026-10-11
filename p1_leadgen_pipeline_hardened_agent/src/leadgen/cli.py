"""The `leadgen` command."""

import argparse
import sys

import psycopg
from pydantic import ValidationError

from leadgen import __version__
from leadgen.database import connect
from leadgen.demo_seed import DEMO_MARKETS, DemoSeedRefused, seed_demo_data
from leadgen.migrate import MigrationError, apply_migrations, discover_migrations

EXIT_CONFIG_ERROR = 2
EXIT_RUNTIME_ERROR = 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="leadgen", description="Lead-gen agent.")
    parser.add_argument("--version", action="version", version=f"leadgen {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="command")

    database_parser = commands.add_parser("db", help="database tasks")
    database_commands = database_parser.add_subparsers(dest="db_command", metavar="db_command")
    database_commands.add_parser("migrate", help="apply schema migrations that have not run yet")
    database_commands.add_parser(
        "seed-demo", help="load fictional demo markets and leads into an empty database"
    )
    return parser


def report_database_error(error: Exception) -> int:
    """Print a plain explanation for configuration and connection failures; return the exit code."""
    if isinstance(error, ValidationError):
        print(
            "DATABASE_URL is missing or malformed. Copy .env.example to .env, "
            "or export DATABASE_URL=postgresql://user:password@host:port/database",
            file=sys.stderr,
        )
        return EXIT_CONFIG_ERROR
    print(f"Cannot connect to the database: {error}".strip(), file=sys.stderr)
    print("For local development, start it with: docker compose up -d db", file=sys.stderr)
    return EXIT_RUNTIME_ERROR


def run_migrate() -> int:
    try:
        migrations = discover_migrations()
        with connect() as connection:
            newly_applied = apply_migrations(connection, migrations)
    except (ValidationError, psycopg.OperationalError) as error:
        return report_database_error(error)
    except MigrationError as error:
        print(f"Migration refused: {error}", file=sys.stderr)
        return EXIT_RUNTIME_ERROR

    if not newly_applied:
        print("Database is up to date.")
    for migration in newly_applied:
        print(f"Applied {migration.name}")
    return 0


def run_seed_demo() -> int:
    try:
        with connect() as connection:
            lead_count = seed_demo_data(connection)
    except (ValidationError, psycopg.OperationalError) as error:
        return report_database_error(error)
    except psycopg.errors.UndefinedTable:
        print("The schema is missing. Run `leadgen db migrate` first.", file=sys.stderr)
        return EXIT_RUNTIME_ERROR
    except DemoSeedRefused as error:
        print(f"Demo seed refused: {error}", file=sys.stderr)
        return EXIT_RUNTIME_ERROR

    print(f"Loaded {len(DEMO_MARKETS)} demo markets and {lead_count} demo leads.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if arguments.command == "db" and arguments.db_command == "migrate":
        return run_migrate()
    if arguments.command == "db" and arguments.db_command == "seed-demo":
        return run_seed_demo()
    parser.print_help()
    return 0
