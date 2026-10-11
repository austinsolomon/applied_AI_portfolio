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


def test_db_migrate_on_an_empty_database(empty_database_url: str, tmp_path: Path) -> None:
    completed = run_leadgen(
        "db", "migrate", env={"DATABASE_URL": empty_database_url, "PATH": ""}, cwd=tmp_path
    )

    # No migrations ship yet, so a fresh database is already up to date.
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "Database is up to date.\n"


def test_db_migrate_without_database_url_explains_the_fix(tmp_path: Path) -> None:
    completed = run_leadgen("db", "migrate", env={"PATH": ""}, cwd=tmp_path)

    assert completed.returncode == 2
    assert "DATABASE_URL is missing or malformed" in completed.stderr
