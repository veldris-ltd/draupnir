"""Runtime configuration, read from the environment.

Every setting carries the `DRAUPNIR_` prefix so that a container inherits
nothing by accident.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process configuration."""

    model_config = SettingsConfigDict(
        env_prefix="DRAUPNIR_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Literal["development", "test", "production"] = "development"
    site_id: str = "sindri"

    database_url: str = "postgresql+asyncpg://draupnir:draupnir@localhost:5432/draupnir"
    database_url_sync: str = "postgresql+psycopg://draupnir:draupnir@localhost:5432/draupnir"

    object_store_endpoint: str = "localhost:9000"
    object_store_access_key: str = "draupnir"
    object_store_secret_key: str = "draupnir-dev-secret"  # noqa: S105
    object_store_bucket: str = "draupnir"
    object_store_secure: bool = False

    api_host: str = "127.0.0.1"
    api_port: int = 8000
    log_level: str = Field(default="info")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, read once."""
    return Settings()
