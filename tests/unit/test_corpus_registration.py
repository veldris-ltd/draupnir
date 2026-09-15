"""What a corpus is registered and judged from, before a decision is taken. RF-43.

The register's DRAFT -> CORPUS_REGISTERED record, shared by the procedure and
the worker; the register built from what a site's chain projects; and the base
model declarations the licence decision judges alongside the sources.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime

import pytest

from draupnir.core.domain.sources import ProjectedSource
from draupnir.core.domain.states import RunState
from draupnir.hamarr import tiers
from draupnir.hodd.register import LicenceRegister, SourceRecord

pytestmark = pytest.mark.unit

AT = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)


def record(url: str, digest: str, *, licence: str = "OGL-UK-3.0") -> SourceRecord:
    return SourceRecord(
        id=uuid.uuid4(),
        jurisdiction="GBR",
        url=url,
        licence_spdx=licence,
        attribution_required=True,
        retrieved_at=AT,
        sha256=digest,
        personal_data=False,
    )


def test_the_registration_records_every_source_digest_and_who_registered_them() -> None:
    register = LicenceRegister([record("https://a", "a" * 64), record("https://b", "b" * 64)])

    facts, payload = register.corpus_registration(curator="curator@veldris.internal")

    assert facts == {"sources_without_declaration": []}
    assert sorted(payload["sources"]) == ["a" * 64, "b" * 64]
    assert payload["curator"] == "curator@veldris.internal"
    expected = hashlib.sha256(("a" * 64 + "\n" + "b" * 64).encode()).hexdigest()
    assert payload["source_sha256"] == expected


def test_a_published_raw_corpus_is_the_corpus_digest_recorded() -> None:
    register = LicenceRegister([record("https://a", "a" * 64)])
    _, payload = register.corpus_registration(curator="c", corpus_sha256="f" * 64)
    assert payload["source_sha256"] == "f" * 64


def test_a_source_with_no_licence_declared_is_named_for_the_guard() -> None:
    register = LicenceRegister([record("https://blank", "c" * 64, licence=" ")])
    facts, _ = register.corpus_registration(curator="c")
    assert facts == {"sources_without_declaration": ["https://blank"]}


def test_the_register_is_built_from_the_sources_a_chain_projects() -> None:
    projected = ProjectedSource(
        id=uuid.uuid4(),
        site_id="sindri",
        jurisdiction="NZL",
        url="https://legislation.govt.nz",
        licence_spdx="CC-BY-4.0",
        attribution_required=True,
        retrieved_at=AT,
        sha256="d" * 64,
        personal_data=True,
        dpia_ref="DPIA-2026-101",
        residency_constraint=("sindri",),
        state=RunState.DRAFT,
    )

    (built,) = LicenceRegister.from_projection([projected])

    assert built.id == projected.id
    assert built.as_mapping()["licenceSpdx"] == "CC-BY-4.0"
    assert built.as_mapping()["personalData"] is True
    assert built.dpia_ref == "DPIA-2026-101"
    assert built.residency_constraint == ("sindri",)


@pytest.mark.parametrize("base", list(tiers.BaseModel))
def test_every_base_declares_a_licence_by_name_and_by_address(base: tiers.BaseModel) -> None:
    declared = tiers.BASE_LICENCE[base]
    by_name = tiers.base_model_facts(str(base))
    by_address = tiers.base_model_facts(f"hodd://sindri/models/core/{base}")

    assert by_name is not None
    assert by_address is not None
    assert by_name["licenceSpdx"] == by_address["licenceSpdx"] == declared.spdx
    assert by_address["url"] == f"hodd://sindri/models/core/{base}"
    assert by_name["kind"] == "base_model"
    assert by_name["personalData"] is False


def test_a_base_nobody_declared_has_no_facts() -> None:
    assert tiers.base_model_facts("hodd://sindri/models/core/SOMEBODY-ELSES-BASE") is None


def test_each_tier_s_base_is_the_one_whose_licence_is_declared() -> None:
    """The address a submitted specification carries resolves to a declaration."""
    for jurisdiction in ("GBR", "NZL"):
        assert tiers.base_model_facts(tiers.base_artefact(jurisdiction, site="brokkr")) is not None
