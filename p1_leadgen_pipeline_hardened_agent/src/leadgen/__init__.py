"""Lead-gen agent: demand gaps → sourcing → dedupe → enrichment → qualification → CRM."""

from importlib.metadata import version

# Single source of truth for the version is pyproject.toml; read it from the installed package.
__version__ = version("leadgen")


def main() -> None:
    """Entry point for the `leadgen` command. Subcommands are added in later steps."""
    print(f"leadgen {__version__}")
