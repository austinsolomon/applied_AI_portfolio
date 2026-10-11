"""The `leadgen` command."""

import argparse
import json
import subprocess
import sys
import uuid
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

import anthropic
import psycopg
from pydantic import ValidationError

from leadgen import __version__
from leadgen.contacts import (
    BlitzApiError,
    BlitzAuthenticationError,
    BlitzClient,
    BlitzOutOfRecords,
    enrich_contact,
)
from leadgen.database import connect
from leadgen.demo_seed import DEMO_MARKETS, DemoSeedRefused, seed_demo_data
from leadgen.migrate import MigrationError, apply_migrations, discover_migrations
from leadgen.places import SNAPSHOT_CENSUS_YEAR, load_places
from leadgen.raw_agent_loop import AgentLoopError, AskResult, ask_about_gaps, total_spend_usd
from leadgen.settings import get_settings

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
    database_commands.add_parser(
        "load-places", help="load the packaged US Census places snapshot (safe to repeat)"
    )
    commands.add_parser("dashboard", help="open the demand dashboard in the browser")

    ask_parser = commands.add_parser(
        "ask", help="ask Claude about the demand gaps (paid: calls the Claude API)"
    )
    ask_parser.add_argument("question", help='e.g. "Which market should we source first?"')
    ask_parser.add_argument("--model", help="Claude model id (default: LEADGEN_MODEL)")
    ask_parser.add_argument(
        "--max-usd", type=Decimal, default=Decimal("0.05"), help="dollar cap for this run"
    )
    ask_parser.add_argument("--max-turns", type=int, default=5, help="maximum model calls")
    ask_parser.add_argument(
        "--effort", default="low", choices=["low", "medium", "high", "xhigh", "max"]
    )

    enrich_parser = commands.add_parser(
        "enrich", help="look up phone and email for a business website (paid: calls BlitzAPI)"
    )
    enrich_parser.add_argument("website", help='e.g. "https://www.cedar-massage.example"')
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


def run_load_places() -> int:
    try:
        with connect() as connection:
            place_count = load_places(connection)
    except (ValidationError, psycopg.OperationalError) as error:
        return report_database_error(error)
    except psycopg.errors.UndefinedTable:
        print("The schema is missing. Run `leadgen db migrate` first.", file=sys.stderr)
        return EXIT_RUNTIME_ERROR

    print(f"Loaded {place_count} US Census places ({SNAPSHOT_CENSUS_YEAR}, population >= 5,000).")
    return 0


def run_ask(arguments: argparse.Namespace) -> int:
    try:
        settings = get_settings()
    except ValidationError as error:
        return report_database_error(error)
    if settings.anthropic_api_key is None:
        print("ANTHROPIC_API_KEY is not set. Add it to .env (never commit it).", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    workspace_header = (
        {"anthropic-workspace-id": settings.anthropic_workspace_id}
        if settings.anthropic_workspace_id
        else {}
    )
    client = anthropic.Anthropic(
        api_key=settings.anthropic_api_key.get_secret_value(), default_headers=workspace_header
    )
    model = arguments.model or settings.leadgen_model
    try:
        with connect() as connection:
            try:
                result = ask_about_gaps(
                    arguments.question,
                    connection=connection,
                    messages_api=client.messages,
                    model=model,
                    max_usd=arguments.max_usd,
                    max_turns=arguments.max_turns,
                    effort=arguments.effort,
                )
            finally:
                spent_overall = total_spend_usd(connection)
    except psycopg.OperationalError as error:
        return report_database_error(error)
    except anthropic.AuthenticationError:
        print("The Claude API rejected the API key. Check ANTHROPIC_API_KEY.", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    except anthropic.BadRequestError as error:
        if "credit balance" in str(error.message).lower():
            print("The Anthropic account is out of API credit.", file=sys.stderr)
        else:
            print(f"The Claude API rejected the request: {error.message}", file=sys.stderr)
        return EXIT_RUNTIME_ERROR
    except anthropic.APIError as error:
        print(f"Claude API error: {error}", file=sys.stderr)
        return EXIT_RUNTIME_ERROR
    except AgentLoopError as error:
        print(f"Stopped: {error}", file=sys.stderr)
        print(f"Spend recorded so far, all runs: ${spent_overall:.4f}", file=sys.stderr)
        return EXIT_RUNTIME_ERROR

    print_ask_result(result, spent_overall)
    return 0


def print_ask_result(result: AskResult, spent_overall: Decimal) -> None:
    assert result.recommendation is not None
    print(result.recommendation.model_dump_json(indent=2))
    print()
    for call in result.calls:
        print(
            f"call {call.call_number}: {call.stop_reason:<9} "
            f"in {call.usage.input_tokens:>5} · cache read {call.usage.cache_read_input_tokens:>5} "
            f"· out {call.usage.output_tokens:>5} tokens · ${call.cost_usd:.5f}"
        )
    print(f"run {result.run_id} on {result.model}: ${result.total_cost_usd:.5f}")
    print(f"all runs recorded in the ledger: ${spent_overall:.5f}")


def run_enrich(arguments: argparse.Namespace) -> int:
    try:
        settings = get_settings()
    except ValidationError as error:
        return report_database_error(error)
    if settings.blitz_api_key is None:
        print("BLITZ_API_KEY is not set. Add it to .env (never commit it).", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    client = BlitzClient(settings.blitz_api_key.get_secret_value())
    run_id = uuid.uuid4()
    try:
        with connect() as connection:
            lookup = enrich_contact(
                arguments.website,
                connection=connection,
                client=client,
                run_id=run_id,
                usd_per_record=settings.blitz_usd_per_record,
            )
    except psycopg.OperationalError as error:
        return report_database_error(error)
    except ValueError as error:
        print(f"Cannot enrich: {error}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    except BlitzAuthenticationError as error:
        print(error, file=sys.stderr)
        return EXIT_CONFIG_ERROR
    except BlitzOutOfRecords as error:
        print(error, file=sys.stderr)
        return EXIT_RUNTIME_ERROR
    except BlitzApiError as error:
        print(f"Enrichment failed: {error}", file=sys.stderr)
        return EXIT_RUNTIME_ERROR

    print(json.dumps(asdict(lookup), indent=2, default=str))
    source = "cache" if lookup.from_cache else f"BlitzAPI, run {run_id}"
    print(f"{lookup.records_used} records used ({source})")
    if client.last_records_remaining is not None:
        print(f"records remaining on the plan: {client.last_records_remaining}")
    return 0


def run_dashboard() -> int:
    dashboard_script = Path(__file__).with_name("dashboard.py")
    # Bound to this machine only; the dashboard is not meant to be reachable from the network.
    return subprocess.call(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(dashboard_script),
            "--server.address",
            "localhost",
            "--browser.gatherUsageStats",
            "false",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if arguments.command == "db" and arguments.db_command == "migrate":
        return run_migrate()
    if arguments.command == "db" and arguments.db_command == "seed-demo":
        return run_seed_demo()
    if arguments.command == "db" and arguments.db_command == "load-places":
        return run_load_places()
    if arguments.command == "dashboard":
        return run_dashboard()
    if arguments.command == "ask":
        return run_ask(arguments)
    if arguments.command == "enrich":
        return run_enrich(arguments)
    parser.print_help()
    return 0
