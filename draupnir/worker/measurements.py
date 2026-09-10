"""Where a periodic duty's latest reading is kept. RF-18.

`/metrics` had no DRAUPNIR collector at all. Most of SAD 11.3's signals can be
read out of tables that already exist, but two cannot: verifying the chain is
the hourly duty's entire cost, and asking an NFS mount how full it is can block
uninterruptibly -- and a scrape that blocks is a target Prometheus marks down,
reporting the control plane as gone because the vault was slow.

So the worker writes what it measured and the API reads it. One row per site
per duty, overwritten each tick.

**In the duty's own transaction.** The store is built on the connection the
orchestrator is using, so a reading and the ledger entry that accompanies an
alarm commit or roll back together. A measurement written by a transaction that
then failed would have `/metrics` reporting a vault reading from a tick that
did not happen.

**Not the ledger.** The chain records state transitions and a vault at forty
per cent is not one; SAD 11.3's recording rule is exactly why duties log what
they find and record only alarms. Appending a reading every fifteen minutes
would add thirty-five thousand entries a year per forge, all of them saying
nothing happened, to a chain whose value is that reading it is worth the time.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, text

#: Upsert, because there is exactly one current reading per duty. `ON CONFLICT`
#: rather than delete-then-insert: two workers ticking at once is the ordinary
#: case this system is built for (SAD 5.1), and the pair would race in the gap.
_RECORD = text(
    "INSERT INTO duty_measurement (site_id, duty, measured_at, alarm, measurements) "
    "VALUES (:site_id, :duty, :measured_at, :alarm, CAST(:measurements AS jsonb)) "
    "ON CONFLICT (site_id, duty) DO UPDATE SET "
    "measured_at = EXCLUDED.measured_at, "
    "alarm = EXCLUDED.alarm, "
    "measurements = EXCLUDED.measurements"
)


@dataclass(frozen=True, slots=True)
class MeasurementStore:
    """Records the latest reading from each duty, on one connection."""

    connection: Connection

    def record(
        self,
        *,
        site_id: str,
        duty: str,
        measured_at: datetime,
        alarm: bool,
        measurements: dict[str, Any],
    ) -> None:
        """Write this duty's current reading, replacing the one before it."""
        self.connection.execute(
            _RECORD,
            {
                "site_id": site_id,
                "duty": duty,
                "measured_at": measured_at,
                "alarm": alarm,
                # `default=str` so a measurement carrying a `Path` or a
                # `datetime` is written rather than raising. A duty that
                # measured something and could not record it is the failure
                # this whole table exists to avoid, and refusing over a type
                # would trade a signal for a stack trace.
                "measurements": json.dumps(measurements, default=str),
            },
        )


__all__ = ["MeasurementStore"]
