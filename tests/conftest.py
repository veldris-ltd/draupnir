"""Shared fixtures.

Integration tests are separated from every other level because they are the
only ones that need Docker. `pytest tests/unit tests/property tests/contract`
runs on a machine with nothing installed but Python.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest


@pytest.fixture
def moment() -> datetime:
    """A fixed, offset-aware instant. SAD 11E.2 forbids naive timestamps."""
    return datetime(2026, 3, 2, 9, 0, tzinfo=UTC)
