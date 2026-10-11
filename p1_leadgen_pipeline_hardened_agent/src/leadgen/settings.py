"""Typed runtime configuration, read from environment variables or a local `.env` file."""

from functools import lru_cache

from pydantic import PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Every setting the application reads. A missing or malformed value fails at startup."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: PostgresDsn


@lru_cache
def get_settings() -> Settings:
    """Load settings once per process."""
    return Settings()
