"""The fold that projects the licence register from the chain. RF-42.

`registerSource` recorded an entry and wrote no row, so a source registered on
an estate appeared nowhere. These tests pin what the fold makes of a
registration, which state a source follows its corpus into, and what it refuses
to complete.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from draupnir.core.domain import sources
from draupnir.core.domain.ledger import GENESIS_HASH, LedgerEntry
from draupnir.core.domain.states import RunState

pytestmark = pytest.mark.unit

AT = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)
SOURCE = uuid.UUID("019cf270-ba80-76c9-84ca-7374e16c7640")
OTHER = uuid.UUID("019cf270-ba80-76c9-84ca-7374e16c7641")
RUN = uuid.UUID("019cf270-ba80-76c9-84ca-7374e16c7642")


def entry(
    seq: int, subject_type: str, subject_id: uuid.UUID, transition: str, payload: dict[str, Any]
) -> LedgerEntry:
    return LedgerEntry(
        id=uuid.uuid4(),
        site_id="sindri",
        seq=seq,
        prev_hash=GENESIS_HASH,
        entry_hash="b" * 64,
        ts=AT + timedelta(minutes=seq),
        actor="curator@veldris.internal",
        subject_type=subject_type,
        subject_id=str(subject_id),
        transition=transition,
        payload=payload,
    )


def registration(seq: int, source: uuid.UUID = SOURCE, **changes: Any) -> LedgerEntry:
    """A `registerSource` entry, with the payload the API records."""
    payload: dict[str, Any] = {
        "jurisdiction": "GBR",
        "url": "https://www.legislation.gov.uk/ukpga",
        "licence_spdx": "OGL-UK-3.0",
        "attribution_required": True,
        "personal_data": False,
        "dpia_ref": None,
        "sha256": "a" * 64,
        "retrieved_at": "2026-03-02T09:00:00+00:00",
        "residency_constraint": ["sindri"],
    }
    payload.update(changes)
    return entry(seq, "source", source, sources.REGISTERED, payload)


def run_entry(seq: int, transition: str, payload: dict[str, Any] | None = None) -> LedgerEntry:
    return entry(seq, "run", RUN, transition, payload or {})


def registered_run(seq: int, name: str = "cim-gbr-v0.9") -> LedgerEntry:
    return run_entry(seq, "->DRAFT", {"name": name, "spec_hash": "c" * 64, "kind": "adapter"})


def test_a_registration_is_a_draft_row_holding_the_facts_it_recorded() -> None:
    (row,) = sources.fold([registration(1)])

    assert row.id == SOURCE
    assert row.site_id == "sindri"
    assert row.jurisdiction == "GBR"
    assert row.licence_spdx == "OGL-UK-3.0"
    assert row.attribution_required is True
    assert row.residency_constraint == ("sindri",)
    assert row.retrieved_at == AT
    assert row.state is RunState.DRAFT


def test_a_source_follows_its_corpus_through_the_licence_decision_and_curation() -> None:
    walked = [
        registration(1),
        registered_run(2),
        run_entry(3, "DRAFT->CORPUS_REGISTERED"),
        run_entry(4, "CORPUS_REGISTERED->LICENCE_CLEARED"),
    ]
    assert sources.fold(walked)[0].state is RunState.LICENCE_CLEARED

    walked.append(run_entry(5, "LICENCE_CLEARED->CURATED"))
    assert sources.fold(walked)[0].state is RunState.CURATED


def test_a_refused_corpus_quarantines_its_sources() -> None:
    (row,) = sources.fold(
        [
            registration(1),
            registered_run(2),
            run_entry(3, "DRAFT->CORPUS_REGISTERED"),
            run_entry(4, "CORPUS_REGISTERED->QUARANTINED", {"rule": "licence-policy"}),
        ]
    )
    assert row.state is RunState.QUARANTINED


def test_only_the_corpus_of_the_source_s_own_jurisdiction_moves_it() -> None:
    (gbr, irl) = sources.fold(
        [
            registration(1),
            registration(2, OTHER, jurisdiction="IRL", url="https://www.irishstatutebook.ie"),
            registered_run(3, name="cim-irl-v0.2"),
            run_entry(4, "DRAFT->CORPUS_REGISTERED"),
        ]
    )
    assert gbr.state is RunState.DRAFT
    assert irl.state is RunState.CORPUS_REGISTERED


def test_a_decision_taken_before_the_source_was_registered_did_not_judge_it() -> None:
    (row,) = sources.fold(
        [
            registered_run(1),
            run_entry(2, "DRAFT->CORPUS_REGISTERED"),
            run_entry(3, "CORPUS_REGISTERED->LICENCE_CLEARED"),
            registration(4),
        ]
    )
    assert row.state is RunState.DRAFT


def test_a_run_whose_name_encodes_no_jurisdiction_moves_nothing() -> None:
    (row,) = sources.fold(
        [
            registration(1),
            registered_run(2, name="midgard-core"),
            run_entry(3, "DRAFT->CORPUS_REGISTERED"),
        ]
    )
    assert row.state is RunState.DRAFT


def test_run_transitions_that_are_not_about_the_corpus_leave_the_register_alone() -> None:
    folded = [registration(1), registered_run(2), run_entry(3, "CURATED->QUEUED")]
    assert sources.fold(folded)[0].state is RunState.DRAFT
    assert not sources.concerns(folded[2])
    assert sources.concerns(folded[0])
    assert sources.concerns(run_entry(4, "CORPUS_REGISTERED->LICENCE_CLEARED"))


@pytest.mark.parametrize(
    "changes",
    [
        {"personal_data": True, "dpia_ref": None},
        {"jurisdiction": "gb"},
        {"url": ""},
        {"licence_spdx": None},
        {"attribution_required": "yes"},
        {"retrieved_at": "2026-03-02T09:00:00"},
        {"residency_constraint": "sindri"},
    ],
    ids=[
        "personal-data-without-dpia",
        "not-iso3",
        "no-url",
        "no-licence",
        "undetermined-attribution",
        "no-offset",
        "residency-not-a-list",
    ],
)
def test_a_registration_recording_less_than_the_facts_is_not_a_row(
    changes: dict[str, Any],
) -> None:
    assert sources.fold([registration(1, **changes)]) == ()


def test_a_registration_before_residency_was_recorded_is_unconstrained() -> None:
    old = registration(1)
    payload = dict(old.payload)
    del payload["residency_constraint"]
    (row,) = sources.fold([entry(1, "source", SOURCE, sources.REGISTERED, payload)])
    assert row.residency_constraint == ()


def test_the_seed_s_former_source_entries_are_not_registrations() -> None:
    """The seed recorded `DRAFT->CURATED` against a source; the API never has."""
    fabricated = entry(1, "source", SOURCE, "DRAFT->CURATED", registration(1).payload)
    assert sources.fold([fabricated]) == ()


def test_the_jurisdiction_is_parsed_from_the_run_name_or_not_at_all() -> None:
    assert sources.jurisdiction_of("cim-gbr-v0.4") == "GBR"
    assert sources.jurisdiction_of("midgard-core") is None
