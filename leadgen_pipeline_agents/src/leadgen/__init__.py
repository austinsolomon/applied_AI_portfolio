"""Lead-gen agent: demand gaps → sourcing → dedupe → enrichment → qualification → CRM."""

from importlib.metadata import PackageNotFoundError, version

# Single source of truth for the version is pyproject.toml; read it from the installed package.
# The hosted dashboard imports the source without installing it, so it has no version.
try:
    __version__ = version("leadgen")
except PackageNotFoundError:
    __version__ = "unknown (not installed)"
