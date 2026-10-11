"""The `leadgen` command: version, and `db migrate` end to end."""

import shutil
import subprocess
import tomllib
from pathlib import Path

import leadgen

PYPROJECT_PATH = Path(__file__).resolve().parents[1] / "pyproject.toml"


def run_leadgen(
    *arguments: str, env: dict[str, str] | None = None, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    command_path = shutil.which("leadgen")
    assert command_path is not None, "the `leadgen` console script is not installed"
    return subprocess.run(
        [command_path, *arguments], capture_output=True, text=True, env=env, cwd=cwd
    )


def test_version_flag_prints_version() -> None:
    completed = run_leadgen("--version")

    assert completed.returncode == 0
    assert completed.stdout == f"leadgen {leadgen.__version__}\n"


def test_reported_version_matches_pyproject() -> None:
    project_table = tomllib.loads(PYPROJECT_PATH.read_text())["project"]

    assert leadgen.__version__ == project_table["version"]


def test_db_migrate_applies_the_schema_to_an_empty_database(
    empty_database_url: str, tmp_path: Path
) -> None:
    completed = run_leadgen(
        "db", "migrate", env={"DATABASE_URL": empty_database_url, "PATH": ""}, cwd=tmp_path
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.splitlines() == [
        "Applied 0001_crm_core.sql",
        "Applied 0002_gap_view_short_by.sql",
        "Applied 0003_cost_ledger.sql",
        "Applied 0004_places.sql",
    ]


def test_db_seed_demo_after_migrate(empty_database_url: str, tmp_path: Path) -> None:
    environment = {"DATABASE_URL": empty_database_url, "PATH": ""}
    run_leadgen("db", "migrate", env=environment, cwd=tmp_path)

    completed = run_leadgen("db", "seed-demo", env=environment, cwd=tmp_path)

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.startswith("Loaded 8 demo markets and ")


def test_db_load_places_after_migrate(empty_database_url: str, tmp_path: Path) -> None:
    environment = {"DATABASE_URL": empty_database_url, "PATH": ""}
    run_leadgen("db", "migrate", env=environment, cwd=tmp_path)

    completed = run_leadgen("db", "load-places", env=environment, cwd=tmp_path)

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.startswith("Loaded ")
    assert "US Census places (2020, population >= 5,000)" in completed.stdout


def test_db_load_places_before_migrate_explains_the_fix(
    empty_database_url: str, tmp_path: Path
) -> None:
    completed = run_leadgen(
        "db", "load-places", env={"DATABASE_URL": empty_database_url, "PATH": ""}, cwd=tmp_path
    )

    assert completed.returncode == 1
    assert "Run `leadgen db migrate` first" in completed.stderr


def test_db_seed_demo_before_migrate_explains_the_fix(
    empty_database_url: str, tmp_path: Path
) -> None:
    completed = run_leadgen(
        "db", "seed-demo", env={"DATABASE_URL": empty_database_url, "PATH": ""}, cwd=tmp_path
    )

    assert completed.returncode == 1
    assert "Run `leadgen db migrate` first" in completed.stderr


def test_db_migrate_without_database_url_explains_the_fix(tmp_path: Path) -> None:
    completed = run_leadgen("db", "migrate", env={"PATH": ""}, cwd=tmp_path)

    assert completed.returncode == 2
    assert "DATABASE_URL is missing or malformed" in completed.stderr


def test_ask_without_an_api_key_explains_the_fix(empty_database_url: str, tmp_path: Path) -> None:
    completed = run_leadgen(
        "ask",
        "Which market first?",
        env={"DATABASE_URL": empty_database_url, "PATH": ""},
        cwd=tmp_path,
    )

    assert completed.returncode == 2
    assert "ANTHROPIC_API_KEY is not set" in completed.stderr
