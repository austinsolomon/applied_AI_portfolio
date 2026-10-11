"""The package's public surface: the `leadgen` command and its version."""

import shutil
import subprocess
import tomllib
from pathlib import Path

import leadgen

PYPROJECT_PATH = Path(__file__).resolve().parents[1] / "pyproject.toml"


def test_leadgen_command_prints_version() -> None:
    command_path = shutil.which("leadgen")
    assert command_path is not None, "the `leadgen` console script is not installed"

    completed = subprocess.run([command_path], capture_output=True, text=True, check=True)

    assert completed.stdout == f"leadgen {leadgen.__version__}\n"


def test_reported_version_matches_pyproject() -> None:
    project_table = tomllib.loads(PYPROJECT_PATH.read_text())["project"]

    assert leadgen.__version__ == project_table["version"]
