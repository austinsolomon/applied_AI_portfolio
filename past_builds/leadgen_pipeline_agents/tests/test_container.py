"""The container image: its pins agree with the rest of the toolchain, its build context never
includes secrets, and (when Docker is available and opted in) it builds and runs `leadgen`."""

import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

import leadgen

PROJECT_DIR = Path(__file__).resolve().parents[1]
DOCKERFILE = PROJECT_DIR / "Dockerfile"
DOCKERIGNORE = PROJECT_DIR / ".dockerignore"
CI_WORKFLOW = PROJECT_DIR.parent / ".github" / "workflows" / "ci.yml"


def dockerfile_arg(name: str) -> str:
    match = re.search(rf"^ARG {name}=(\S+)$", DOCKERFILE.read_text(), flags=re.MULTILINE)
    assert match is not None, f"Dockerfile has no `ARG {name}=...` line"
    return match.group(1)


def test_python_pin_matches_the_project() -> None:
    assert dockerfile_arg("PYTHON_VERSION") == (PROJECT_DIR / ".python-version").read_text().strip()


def test_uv_pin_matches_ci() -> None:
    ci_pin = re.search(r'version: "(\d+\.\d+\.\d+)"', CI_WORKFLOW.read_text())
    assert ci_pin is not None, "ci.yml has no setup-uv version pin"
    assert dockerfile_arg("UV_VERSION") == ci_pin.group(1)


def test_claude_code_version_is_pinned_exactly() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", dockerfile_arg("CLAUDE_CODE_VERSION"))


def test_build_context_excludes_everything_but_the_package_inputs() -> None:
    rules = [
        line.strip()
        for line in DOCKERIGNORE.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    allowed = {rule[1:] for rule in rules if rule.startswith("!")}

    assert rules[0] == "*", "the first rule must exclude everything; later rules opt files back in"
    assert allowed == {"pyproject.toml", "uv.lock", "README.md", "src/", "config/"}
    assert ".env" in rules and ".env.*" in rules


def test_image_runs_as_a_named_non_root_user_with_leadgen_as_entrypoint() -> None:
    dockerfile = DOCKERFILE.read_text()

    assert "USER leadgen" in dockerfile
    assert 'ENTRYPOINT ["leadgen"]' in dockerfile
    assert dockerfile.index("USER leadgen") < dockerfile.index('ENTRYPOINT ["leadgen"]')


@pytest.mark.skipif(
    shutil.which("docker") is None or os.environ.get("LEADGEN_CONTAINER_TESTS") != "1",
    reason="needs Docker and LEADGEN_CONTAINER_TESTS=1 (the CI workflow builds the image directly)",
)
def test_image_builds_and_reports_the_package_version(empty_database_url: str) -> None:
    tag = f"leadgen-test:{uuid.uuid4().hex[:8]}"
    build = subprocess.run(
        ["docker", "build", "--quiet", "--tag", tag, str(PROJECT_DIR)],
        capture_output=True,
        text=True,
    )
    assert build.returncode == 0, build.stderr
    try:
        version = subprocess.run(
            ["docker", "run", "--rm", tag, "--version"], capture_output=True, text=True
        )
        assert version.returncode == 0, version.stderr
        assert version.stdout == f"leadgen {leadgen.__version__}\n"

        claude_cli = subprocess.run(
            ["docker", "run", "--rm", "--entrypoint", "claude", tag, "--version"],
            capture_output=True,
            text=True,
        )
        assert claude_cli.returncode == 0, claude_cli.stderr
        assert claude_cli.stdout.startswith(dockerfile_arg("CLAUDE_CODE_VERSION"))

        # The throwaway database is on the host; inside the container the host is reachable
        # through Docker's host alias.
        container_database_url = empty_database_url.replace(
            "127.0.0.1", "host.docker.internal"
        ).replace("localhost", "host.docker.internal")
        migrate = subprocess.run(
            ["docker", "run", "--rm", "-e", f"DATABASE_URL={container_database_url}", tag]
            + ["db", "migrate"],
            capture_output=True,
            text=True,
        )
        assert migrate.returncode == 0, migrate.stderr
        assert "Applied 0001_crm_core.sql" in migrate.stdout
    finally:
        subprocess.run(["docker", "rmi", "--force", tag], capture_output=True)
