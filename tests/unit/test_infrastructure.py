"""Configuration and engine construction.

These do not touch a database. `site_scoped_session` does, and is covered by
`tests/integration/test_schema_constraints.py`, which is the only place the row
level security policy can honestly be exercised.
"""

from __future__ import annotations

import pytest

from draupnir.core.infrastructure.config import Settings, get_settings
from draupnir.core.infrastructure.database import create_engine, session_factory


def test_settings_read_the_draupnir_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DRAUPNIR_SITE_ID", "brokkr")
    monkeypatch.setenv("DRAUPNIR_API_PORT", "9999")
    settings = Settings(_env_file=None)
    assert settings.site_id == "brokkr"
    assert settings.api_port == 9999


def test_an_unknown_environment_is_refused() -> None:
    with pytest.raises(ValueError, match="env"):
        Settings(_env_file=None, env="staging")


def test_settings_are_read_once() -> None:
    assert get_settings() is get_settings()


def test_an_engine_is_built_without_connecting() -> None:
    engine = create_engine()
    try:
        assert engine.dialect.name == "postgresql"
        assert engine.dialect.is_async
    finally:
        engine.sync_engine.dispose()


def test_a_session_factory_binds_to_its_engine() -> None:
    engine = create_engine()
    try:
        factory = session_factory(engine)
        assert factory.kw["bind"] is engine
    finally:
        engine.sync_engine.dispose()
