"""The ledger append announces itself, so every API process hears it.

RF-15. The event stream was `STREAMS: dict[str, EventStream]` in module state,
fed by exactly one publisher -- `submitRun` -- with a per-process sequence
counter. Three consequences, and each of them is the stream not being a stream:

* A console connected to API process A never saw anything published in process
  B, and SAD 5.1 runs two to four of them.
* The *worker* is a separate process and performs every state transition, every
  gate decision, every cancellation and every retry. None of them reached the
  stream at all. AC-U4 and AC-N3 passed in end-to-end only because the test
  submitted through the same process that served the stream.
* Sequence numbers were a per-process counter, so a client reconnecting to a
  different process resynchronised against the wrong ordinal -- and was told
  nothing, because the number looked plausible.

**The chain is the source, and it already exists.** Every one of those changes
is an entry in `ledger_entry`, appended in the transaction that made the change.
A trigger that notifies on insert therefore fires exactly when something
happened, exactly once, and after the transaction commits -- so a listener
cannot see an event for a change that was rolled back.

**What travels is the identity, not the payload.** `pg_notify` carries at most
8000 bytes and a ledger payload has no bound: a merge configuration or a set of
gate results would silently exceed it, and a notification that is sometimes
delivered is worse than one that never is. So the notification carries what
identifies the change -- site, sequence, subject, transition, instant -- and a
consumer that needs more re-reads the entry it names.

The sequence is the ledger's. That is what makes `Last-Event-ID` work across a
reconnect to a different process, which was the third defect: two processes
counting independently produce two different meanings for `id: 41`.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-10
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The channel every API process listens on. One channel for every site rather
#: than one per site: a listener filters by the `siteId` in the payload, and a
#: channel per site would need a `LISTEN` per site added and removed as forges
#: are registered -- state in the connection that has to be kept in step with a
#: table.
CHANNEL = "draupnir_ledger"

NOTIFY_FUNCTION = f"""
CREATE OR REPLACE FUNCTION draupnir_notify_ledger_entry() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    body text;
BEGIN
    -- Identity only. `pg_notify` refuses a payload over 8000 bytes and a
    -- ledger payload has no bound, so sending the row would work in testing
    -- and fail on the first merge configuration. A consumer that needs the
    -- payload reads the entry this names.
    body := json_build_object(
        'siteId', NEW.site_id,
        'seq', NEW.seq,
        'subjectType', NEW.subject_type,
        'subjectId', NEW.subject_id,
        'transition', NEW.transition,
        'actor', NEW.actor,
        'at', to_char(NEW.ts AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.USOF')
    )::text;
    PERFORM pg_notify('{CHANNEL}', body);
    RETURN NULL;
END;
$$;
"""

# AFTER INSERT, and per row. After, because a listener must never see an event
# for a transaction that rolled back: `pg_notify` is transactional, so the
# notification is delivered on commit and discarded otherwise. Per row, because
# each entry is one change and a statement-level trigger would coalesce a batch
# append into one notification that named none of them.
NOTIFY_TRIGGER = """
CREATE TRIGGER trg_ledger_entry_notify
AFTER INSERT ON ledger_entry
FOR EACH ROW EXECUTE FUNCTION draupnir_notify_ledger_entry();
"""


def upgrade() -> None:
    """Fire a notification for every appended entry."""
    op.execute(NOTIFY_FUNCTION)
    op.execute(NOTIFY_TRIGGER)


def downgrade() -> None:
    """Refuse. AC-Q6: migrations are forward only.

    Recovery from a bad migration is a restore plus a new forward migration,
    not a downgrade path that has never been exercised.
    """
    raise NotImplementedError("DRAUPNIR migrations are forward only (AC-Q6)")
