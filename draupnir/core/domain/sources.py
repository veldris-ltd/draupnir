"""The licence register, folded from the chain. RF-42.

`registerSource` recorded a `source` entry and wrote no `source` row, and the
seed was the only thing that wrote one. Three things read the table: S04's
register, the lineage that walks back to a jurisdiction's sources, and through
the lineage the training content summary and SBOM of every release package. On
an estate a source registered through the console appeared in none of them.

This is the fold that makes the register a projection, beside RF-33's releases
and RF-41's gate results. Like them it is pure: the same entries in the same
order give the same rows, and it never reads the table it produces.

**What a row is.** A `registered` entry that recorded the facts HODD holds: a
jurisdiction, an address, a declared licence, the attribution and personal data
determinations, a digest and a retrieval time. A personal data determination
without its DPIA reference is not a row. The table refuses one, and the API
refuses to record one, so an entry carrying it was not written by anything that
registers sources and is skipped rather than allowed to stop the fold.

**What state a row holds.** A source is registered as DRAFT and then follows
the corpus it belongs to. SAD 6.1 records the corpus's progress as transitions
of the runs that consume it -- a curator registering the corpus, GLEIPNIR's
licence decision, curation -- and a run's corpus is the jurisdiction its name
encodes. So each of those transitions, on a run of the source's jurisdiction at
the source's site, moves the source to the same state, and the latest wins: a
corpus refused and re-cleared under a newer policy is cleared.

Only transitions recorded *after* the source was registered. A licence decision
taken before a source existed did not judge it, and a register that showed it
cleared would state a judgement nobody made.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Final
from uuid import UUID

from draupnir.core.domain.ledger import LedgerEntry
from draupnir.core.domain.projector import REGISTRATION
from draupnir.core.domain.states import RunState

#: What a registration is about, and the transition it records.
SOURCE_SUBJECT: Final = "source"
REGISTERED: Final = "registered"

#: The corpus transitions of SAD 6.1, and the state each leaves a source in.
FOLLOWS: Final[Mapping[str, RunState]] = {
    f"{RunState.DRAFT}->{RunState.CORPUS_REGISTERED}": RunState.CORPUS_REGISTERED,
    f"{RunState.CORPUS_REGISTERED}->{RunState.LICENCE_CLEARED}": RunState.LICENCE_CLEARED,
    f"{RunState.CORPUS_REGISTERED}->{RunState.QUARANTINED}": RunState.QUARANTINED,
    f"{RunState.LICENCE_CLEARED}->{RunState.CURATED}": RunState.CURATED,
}

#: Run names in this estate are `cim-<iso3>-v<n>`. The projection carries no
#: jurisdiction column -- it is a projection of the ledger, and the ledger
#: records the specification's name -- so the jurisdiction is parsed from the
#: name and is `None` when the name does not encode one. Returning a guess would
#: put a wrong flag beside a run, and here would move another corpus's sources.
_JURISDICTION = re.compile(r"^cim-([a-z]{3})-", re.IGNORECASE)
_ISO3 = re.compile(r"^[A-Z]{3}$")


def jurisdiction_of(name: str) -> str | None:
    """The ISO 3166-1 alpha-3 code a run name encodes, where it encodes one."""
    match = _JURISDICTION.match(name)
    return match.group(1).upper() if match else None


@dataclass(frozen=True, slots=True)
class ProjectedSource:
    """One registered source, as its registration recorded it."""

    id: UUID
    site_id: str
    jurisdiction: str
    url: str
    licence_spdx: str
    attribution_required: bool
    retrieved_at: datetime
    sha256: str
    personal_data: bool
    dpia_ref: str | None
    residency_constraint: tuple[str, ...]
    state: RunState


def concerns(entry: LedgerEntry) -> bool:
    """Whether an entry registers a source or moves a corpus."""
    if entry.subject_type == SOURCE_SUBJECT:
        return entry.transition == REGISTERED
    return entry.subject_type == "run" and entry.transition in FOLLOWS


def fold(entries: Iterable[LedgerEntry]) -> tuple[ProjectedSource, ...]:
    """Fold one site's chain, oldest first, into its register."""
    sources: dict[UUID, ProjectedSource] = {}
    corpus_of: dict[str, str] = {}

    for entry in sorted(entries, key=lambda item: item.seq):
        payload = entry.payload if isinstance(entry.payload, Mapping) else {}

        if entry.subject_type == SOURCE_SUBJECT and entry.transition == REGISTERED:
            registered = _registered(entry, payload)
            if registered is not None:
                sources[registered.id] = registered
            continue

        if entry.subject_type != "run":
            continue
        if entry.transition == REGISTRATION:
            jurisdiction = jurisdiction_of(str(payload.get("name") or ""))
            if jurisdiction is not None:
                corpus_of[entry.subject_id] = jurisdiction
            continue

        target = FOLLOWS.get(entry.transition)
        jurisdiction = corpus_of.get(entry.subject_id)
        if target is None or jurisdiction is None:
            continue
        for source_id, source in sources.items():
            if source.jurisdiction == jurisdiction:
                sources[source_id] = replace(source, state=target)

    return tuple(sources.values())


def _registered(entry: LedgerEntry, payload: Mapping[str, Any]) -> ProjectedSource | None:
    """The row a registration records, or `None` when it recorded less."""
    try:
        source_id = UUID(str(entry.subject_id))
    except ValueError:
        return None

    jurisdiction = payload.get("jurisdiction")
    url = payload.get("url")
    licence = payload.get("licence_spdx")
    digest = payload.get("sha256")
    attribution = payload.get("attribution_required")
    personal_data = payload.get("personal_data")
    dpia_ref = payload.get("dpia_ref")
    retrieved_at = _instant(payload.get("retrieved_at"))
    residency = payload.get("residency_constraint", [])

    if not isinstance(jurisdiction, str) or not _ISO3.match(jurisdiction):
        return None
    if not all(isinstance(value, str) and value for value in (url, licence, digest)):
        return None
    if not isinstance(attribution, bool) or not isinstance(personal_data, bool):
        return None
    if dpia_ref is not None and not isinstance(dpia_ref, str):
        return None
    if personal_data and not dpia_ref:
        return None
    if retrieved_at is None:
        return None
    if not isinstance(residency, list) or not all(isinstance(site, str) for site in residency):
        return None

    return ProjectedSource(
        id=source_id,
        site_id=str(entry.site_id),
        jurisdiction=jurisdiction,
        url=str(url),
        licence_spdx=str(licence),
        attribution_required=attribution,
        retrieved_at=retrieved_at,
        sha256=str(digest),
        personal_data=personal_data,
        dpia_ref=dpia_ref or None,
        residency_constraint=tuple(residency),
        state=RunState.DRAFT,
    )


def _instant(value: object) -> datetime | None:
    """An offset-aware instant, or `None`: a retrieval time without an offset is not one."""
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else None
    if not value:
        return None
    try:
        found = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return found if found.tzinfo is not None else None


__all__ = [
    "FOLLOWS",
    "REGISTERED",
    "SOURCE_SUBJECT",
    "ProjectedSource",
    "concerns",
    "fold",
    "jurisdiction_of",
]
