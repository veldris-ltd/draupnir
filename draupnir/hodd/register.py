"""The licence register: what was declared about a source, and nothing more.

SAD Decision S4: "GLEIPNIR judges and never executes; HODD records and never
judges." This module is the recording half, and it is written so that the
judging half cannot creep into it.

There is no verdict type here, no rule, no allow list and no comparison of one
licence against another. A `SourceRecord` carries an SPDX identifier as a
string, an attribution flag as a boolean, and a personal data determination as
a boolean, because those are facts a curator establishes. Whether that
combination may be trained on is a question with an answer that changes when
policy changes, and an answer stored here would be a stale answer stored
forever.

That separation is what makes a policy change cheap. Re-evaluating every
source against a new licence policy reads these records and writes nothing;
no corpus is re-ingested and no hash is recomputed, because nothing about the
source has changed -- only the question being asked of it.

`tests/unit/test_hodd_records_and_does_not_judge.py` fails the build if a
judgement appears here.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any
from uuid import UUID

from draupnir.core.domain.states import RunState


class RegisterError(Exception):
    """Raised when a source cannot be recorded."""


@dataclass(frozen=True, slots=True)
class SourceRecord:
    """One entry in the licence register. Attributes per SAD 7.1 `source`.

    Every field is something somebody observed or declared. None of them is an
    opinion about whether the source may be used.
    """

    id: UUID
    jurisdiction: str
    url: str
    #: The SPDX identifier as declared. Held as a string, not parsed into a
    #: permission: `CC-BY-SA-4.0` means different things under different
    #: policies, and the register is not the place that decides which.
    licence_spdx: str
    attribution_required: bool
    retrieved_at: datetime
    sha256: str
    personal_data: bool
    dpia_ref: str | None = None
    #: Site identifiers permitted to hold this corpus. Empty means
    #: unconstrained. A fact from a jurisdiction, checked at planning by the
    #: site resolver, not judged here (SAD 11C).
    residency_constraint: tuple[str, ...] = ()
    #: Where the corpus lifecycle has reached. A state, not a verdict: GLEIPNIR
    #: moves a source to QUARANTINED, and the register records that it did.
    state: RunState = RunState.DRAFT
    #: Anything else the ingest observed, kept verbatim.
    facts: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Refuse a record that is internally impossible."""
        if self.personal_data and not self.dpia_ref:
            msg = (
                f"source {self.url} is declared as holding personal data but names no "
                "DPIA. The determination and its reference are one fact, not two."
            )
            raise RegisterError(msg)
        if self.retrieved_at.tzinfo is None:
            msg = "register timestamps carry an explicit offset (SAD 11E.2)"
            raise RegisterError(msg)

    def as_mapping(self) -> dict[str, Any]:
        """The wire shape, for a ledger payload or the Article 53 summary."""
        return {
            "id": str(self.id),
            "jurisdiction": self.jurisdiction,
            "url": self.url,
            "licenceSpdx": self.licence_spdx,
            "attributionRequired": self.attribution_required,
            "retrievedAt": self.retrieved_at.isoformat(),
            "sha256": self.sha256,
            "personalData": self.personal_data,
            "dpiaRef": self.dpia_ref,
            "residencyConstraint": list(self.residency_constraint),
            "state": str(self.state),
        }

    def with_state(self, state: RunState) -> SourceRecord:
        """Return a copy in a new lifecycle state.

        The register records the move. What justified it is the policy
        decision GLEIPNIR wrote to the ledger, and it lives there.
        """
        return replace(self, state=state)


class LicenceRegister:
    """Every source HODD has recorded, in memory.

    Deliberately a plain collection over `SourceRecord`. Persistence is the
    repository's business, and keeping the register itself pure is what lets a
    policy re-evaluation run over ten thousand sources in a test without a
    database.
    """

    def __init__(self, records: Iterable[SourceRecord] = ()) -> None:
        """Build a register from records already established."""
        self._records: dict[UUID, SourceRecord] = {record.id: record for record in records}

    def __len__(self) -> int:
        """How many sources are recorded."""
        return len(self._records)

    def __iter__(self) -> Iterator[SourceRecord]:
        """Iterate in identifier order, so a summary is reproducible."""
        return iter(sorted(self._records.values(), key=lambda record: str(record.id)))

    def __contains__(self, source_id: object) -> bool:
        """Whether a source is recorded."""
        return source_id in self._records

    def record(self, source: SourceRecord) -> SourceRecord:
        """Add a source. Recording the same source twice is refused.

        A duplicate is almost always a re-ingest of something already held,
        and silently replacing the first record would discard the retrieval
        date that the retention clock is measured from.
        """
        if source.id in self._records:
            msg = f"source {source.id} is already recorded"
            raise RegisterError(msg)
        self._records[source.id] = source
        return source

    def get(self, source_id: UUID) -> SourceRecord:
        """Return one source, or raise."""
        try:
            return self._records[source_id]
        except KeyError as error:
            msg = f"no source {source_id} is recorded"
            raise RegisterError(msg) from error

    def update(self, source: SourceRecord) -> SourceRecord:
        """Replace a record with a new version of itself."""
        if source.id not in self._records:
            msg = f"no source {source.id} is recorded"
            raise RegisterError(msg)
        self._records[source.id] = source
        return source

    def by_state(self, state: RunState) -> tuple[SourceRecord, ...]:
        """Every source resting in one lifecycle state."""
        return tuple(record for record in self if record.state is state)

    def by_jurisdiction(self, jurisdiction: str) -> tuple[SourceRecord, ...]:
        """Every source for one jurisdiction."""
        return tuple(record for record in self if record.jurisdiction == jurisdiction)

    def licences(self) -> dict[str, int]:
        """How many sources declare each SPDX identifier.

        A count, not an assessment. The Article 53 training content summary is
        built from this, and so is the input GLEIPNIR evaluates.
        """
        counts: dict[str, int] = {}
        for record in self:
            counts[record.licence_spdx] = counts.get(record.licence_spdx, 0) + 1
        return dict(sorted(counts.items()))

    def facts_for_policy(self) -> tuple[dict[str, Any], ...]:
        """Every source rendered as the facts a policy driver evaluates.

        This is the whole of the interface between recording and judging. A
        policy receives these mappings and returns decisions; it is never
        handed the register, and the register never learns what it decided.
        """
        return tuple(record.as_mapping() for record in self)
