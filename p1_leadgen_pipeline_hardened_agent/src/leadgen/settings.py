"""Typed runtime configuration, read from environment variables or a local `.env` file."""

from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from pydantic import PostgresDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Every setting the application reads. A missing or malformed value fails at startup."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: PostgresDsn
    # Only `leadgen ask` needs these. The key stays a SecretStr so it is never printed or logged.
    anthropic_api_key: SecretStr | None = None
    # Required only when the API key is not scoped to a workspace (the API then asks which one).
    anthropic_workspace_id: str | None = None
    leadgen_model: str = "claude-haiku-5-5"
    # The one category this deployment sources; relative paths resolve from the working directory.
    category_config_path: Path = Path("config/category.yaml")
    # Contact enrichment (BlitzAPI). The plan is a flat monthly rate, so the per-record price
    # is whatever the operator chooses to amortize into the ledger; records are always counted.
    blitz_api_key: SecretStr | None = None
    blitz_usd_per_record: Decimal = Decimal(0)

    # Web search and page fetching use Firecrawl when this key is set, otherwise the free backend.
    firecrawl_api_key: SecretStr | None = None
    # Dollars per Firecrawl credit, for the cost ledger. Default: the smallest paid plan
    # (5,000 credits for $16 a month), which errs on the high side for cheaper plans.
    firecrawl_usd_per_credit: Decimal = Decimal("0.0032")


@lru_cache
def get_settings() -> Settings:
    """Load settings once per process."""
    return Settings()
