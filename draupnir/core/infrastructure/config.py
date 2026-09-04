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

    # 127.0.0.1 rather than `localhost`. On Windows `localhost` resolves to
    # ::1 first, the container publishes on IPv4 only, and psycopg waits out
    # the full connect timeout on the v6 attempt before falling back -- which
    # presents as the write path hanging while every read works, because the
    # async driver resolves differently. Naming the address removes the
    # difference rather than leaving it to a resolver.
    database_url: str = "postgresql+asyncpg://draupnir:draupnir@127.0.0.1:5432/draupnir"
    database_url_sync: str = "postgresql+psycopg://draupnir:draupnir@127.0.0.1:5432/draupnir"

    # Where the HODD vault is mounted. Empty means this installation has none,
    # which is the honest answer for a control plane without an estate: the
    # vault checks are then skipped rather than alarming hourly about an NFS
    # export that was never there.
    vault_root: str = ""

    object_store_endpoint: str = "127.0.0.1:9000"
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
