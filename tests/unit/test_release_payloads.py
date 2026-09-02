"""The wire shapes: what the console renders and the release package publishes.

These are not incidental. SAD 8.3 asks for a per jurisdiction gate trend, SAD
11E.2 fixes the API conventions the payloads follow, and AC-F10 requires the
release package to be internally consistent -- which is a claim about exactly
these structures. A payload that is never exercised is a payload that is wrong
the first time somebody looks at it.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest

from draupnir.core.domain.evidence import Evidence
from draupnir.interfaces.types import GateOutcome
from draupnir.raun import baselines as baseline_module
from draupnir.raun import regression, suites
from draupnir.raun.baselines import BaselineRegistry
from draupnir.skidbladnir import facts, lineage, modelcard, publish, sbom
from draupnir.skidbladnir.facts import Fact, FactSet

AT = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)
SUBSTRATE = "5" * 64
MERGED = "e" * 64


# -- baselines --------------------------------------------------------------


@pytest.fixture
def registry() -> BaselineRegistry:
    holder = BaselineRegistry()
    holder.capture(
        baseline_module.capture(
            artefact_sha256=SUBSTRATE,
            artefact_kind="substrate",
            suite="general-core",
            suite_version="2026.01",
            measurements={"E1": 0.78, "E2": 0.74},
            captured_at=AT,
            label="MIDGARD-CORE",
        )
    )
    return holder


def test_the_baseline_payload_carries_its_hash_and_measurements(
    registry: BaselineRegistry,
) -> None:
    payload = registry.as_payload()["baselines"][0]

    assert payload["artefactSha256"] == SUBSTRATE
    assert payload["measurements"] == {"E1": 0.78, "E2": 0.74}
    assert payload["label"] == "MIDGARD-CORE"


def test_a_registry_reports_its_size_and_iterates(registry: BaselineRegistry) -> None:
    assert len(registry) == 1
    assert [item.artefact_sha256 for item in registry] == [SUBSTRATE]


def test_values_for_returns_what_a_suite_evaluation_needs(
    registry: BaselineRegistry,
) -> None:
    assert registry.values_for("general-core", "substrate") == {"E1": 0.78, "E2": 0.74}


def test_find_returns_none_rather_than_raising(registry: BaselineRegistry) -> None:
    assert registry.find("nonexistent", "substrate") is None


def test_a_single_baseline_value_is_reachable_by_gate(registry: BaselineRegistry) -> None:
    baseline = registry.resolve("general-core", "substrate")

    assert baseline.value_for("E1") == 0.78
    assert baseline.value_for("E9") is None


def test_a_naive_capture_timestamp_is_refused() -> None:
    """SAD 11E.2."""
    with pytest.raises(baseline_module.BaselineError, match="explicit offset"):
        baseline_module.capture(
            artefact_sha256=SUBSTRATE,
            artefact_kind="substrate",
            suite="general-core",
            suite_version="2026.01",
            measurements={"E1": 0.5},
            captured_at=datetime(2026, 3, 2, 9, 0),  # noqa: DTZ001
        )


def test_a_baseline_naming_a_malformed_hash_is_refused() -> None:
    with pytest.raises(baseline_module.BaselineError, match="a baseline names its bytes"):
        baseline_module.capture(
            artefact_sha256="short",
            artefact_kind="substrate",
            suite="general-core",
            suite_version="2026.01",
            measurements={"E1": 0.5},
            captured_at=AT,
        )


def test_a_registry_can_be_built_from_a_collection() -> None:
    holder = baseline_module.registry_of(
        [
            baseline_module.capture(
                artefact_sha256=SUBSTRATE,
                artefact_kind="substrate",
                suite="general-core",
                suite_version="2026.01",
                measurements={"E1": 0.5},
                captured_at=AT,
            )
        ]
    )

    assert len(holder) == 1


# -- suites -----------------------------------------------------------------


def test_the_suite_payload_lists_what_it_applies_to_and_feeds() -> None:
    payload = suites.default_registry().as_payload()["suites"][0]

    assert payload["name"] == "general-core"
    assert payload["gates"] == ["E1", "E2", "E3", "E4", "E5", "E6"]
    assert "adapter" in payload["appliesTo"]
    assert payload["driver"] == "raun.lmeval/v1"


def test_a_suite_is_retrievable_by_name_and_version() -> None:
    holder = suites.default_registry()

    assert holder.get("general-core/2026.01").name == "general-core"


def test_an_unknown_suite_key_names_what_is_registered() -> None:
    with pytest.raises(suites.SuiteError, match=re.escape("known: general-core/2026.01")):
        suites.default_registry().get("cim-gbr/2026.01")


def test_a_suite_claiming_an_unevaluable_artefact_kind_is_refused() -> None:
    with pytest.raises(suites.SuiteError, match="not an evaluable artefact kind"):
        suites.Suite(
            name="corpus-suite",
            version="1",
            applies_to=frozenset({"corpus_raw"}),
            gates=("E1",),
        )


# -- regression -------------------------------------------------------------


def evidence(sha: str, measurements: dict[str, float], *, at: datetime = AT) -> Evidence:
    return Evidence(
        artefact_sha256=sha,
        artefact_kind="merged",
        outcomes=tuple(
            GateOutcome(gate=gate, suite_version="2026.01", value=value, passed=True)
            for gate, value in sorted(measurements.items())
        ),
        passed=True,
        suite="general-core",
        suite_version="2026.01",
        evaluated_at=at,
        measurements=measurements,
    )


def test_the_comparison_payload_carries_every_movement() -> None:
    previous = evidence(SUBSTRATE, {"E1": 0.80, "E2": 0.86})
    current = evidence(MERGED, {"E1": 0.74, "E2": 0.90}, at=AT + timedelta(days=1))

    payload = regression.compare(current, previous, compared_at=AT, jurisdiction="GBR").as_payload()

    assert payload["regressed"] is True
    assert payload["jurisdiction"] == "GBR"
    assert {item["gate"] for item in payload["movements"]} == {"E1", "E2"}
    assert "regression against" in payload["summary"]


def test_an_improvement_is_reported_as_such() -> None:
    previous = evidence(SUBSTRATE, {"E1": 0.70})
    current = evidence(MERGED, {"E1": 0.80})

    comparison = regression.compare(current, previous, compared_at=AT)

    assert [item.gate for item in comparison.improvements] == ["E1"]
    assert "1 measurement(s) improved" in comparison.describe()


def test_a_first_release_payload_says_so() -> None:
    payload = regression.compare(evidence(MERGED, {"E1": 0.8}), None, compared_at=AT).as_payload()

    assert payload["firstRelease"] is True
    assert payload["previousSha256"] is None


def test_the_fleet_summary_names_the_jurisdictions_that_regressed() -> None:
    """SAD 8.3's fleet view."""
    regressed = regression.compare(
        evidence(MERGED, {"E1": 0.70}), evidence(SUBSTRATE, {"E1": 0.80}), compared_at=AT
    )
    steady = regression.compare(
        evidence(MERGED, {"E1": 0.80}), evidence(SUBSTRATE, {"E1": 0.80}), compared_at=AT
    )

    summary = regression.summarise({"GBR": regressed, "KEN": steady})

    assert summary["regressed"] == ["GBR"]
    assert set(summary["comparisons"]) == {"GBR", "KEN"}


# -- facts ------------------------------------------------------------------


def test_a_section_built_from_a_mapping_states_every_missing_key() -> None:
    """Iterating the mapping's own keys describes whatever happened to be there."""
    section = facts.from_mapping(
        "identity",
        {"model": "cim-gbr"},
        source="run record",
        expected=("model", "jurisdiction"),
    )

    assert section.get("model").known
    assert not section.get("jurisdiction").known
    assert section.absent[0].name == "jurisdiction"


def test_absences_are_reported_across_sections() -> None:
    sections = [
        FactSet("identity", (Fact.absent("model", "not recorded"),)),
        FactSet("provenance", (Fact.recorded("runId", "r1", "ledger"),)),
    ]

    assert facts.absences(sections) == ("identity.model",)


def test_a_fact_renders_lists_and_booleans_readably() -> None:
    assert Fact.recorded("formats", ["nvfp4", "mlx4"], "run").render() == "nvfp4, mlx4"
    assert Fact.recorded("formats", [], "run").render() == "none"
    assert Fact.recorded("flag", True, "run").render() == "yes"


def test_asking_for_a_fact_a_section_never_assembled_returns_an_absence() -> None:
    section = FactSet("identity", ())

    assert not section.get("model").known
    assert "was assembled for this section" in section.get("model").reason


def test_a_section_reports_its_length_and_rows() -> None:
    section = FactSet("identity", (Fact.recorded("model", "cim-gbr", "run record"),))

    assert len(section) == 1
    assert section.as_rows() == (("model", "cim-gbr"),)
    assert section.complete


# -- lineage, sbom and publish ----------------------------------------------


def chain() -> lineage.Lineage:
    return lineage.build(
        "6" * 64,
        (
            lineage.Node(sha256="1" * 64, kind="source", licence="CC-BY-4.0"),
            lineage.Node(sha256="2" * 64, kind="corpus_curated", derived_from=("1" * 64,)),
            lineage.Node(sha256="3" * 64, kind="base_model", licence="Apache-2.0"),
            lineage.Node(sha256="6" * 64, kind="quantised", derived_from=("2" * 64, "3" * 64)),
        ),
    )


def test_the_lineage_payload_is_the_tree_the_endpoint_returns() -> None:
    payload = chain().as_payload()

    assert payload["schema"] == lineage.SCHEMA
    assert payload["complete"] is True
    assert payload["corpusLicences"] == ["CC-BY-4.0"]
    assert len(payload["nodes"]) == 4


def test_the_roots_are_reachable_and_sorted() -> None:
    roots = chain().chain_to_roots()

    assert [item.kind for item in roots] == ["source", "base_model"]


def test_a_lineage_whose_subject_is_absent_is_refused() -> None:
    with pytest.raises(lineage.LineageError, match="does not contain"):
        lineage.build("9" * 64, (lineage.Node(sha256="1" * 64, kind="source", licence="MIT"),))


def test_a_lineage_can_be_assembled_from_mappings() -> None:
    """The seam: SKIDBLADNIR imports neither HODD nor the ledger."""
    rebuilt = lineage.from_mappings("6" * 64, [item.as_payload() for item in chain().nodes])

    assert rebuilt.complete
    assert rebuilt.licences() == chain().licences()


def test_the_attestation_serialises_to_json() -> None:
    import json

    attestation = lineage.attest(chain(), attested_at=AT, signature="sig")

    assert json.loads(attestation.to_json())["signature"] == "sig"


def test_the_sbom_reports_its_licences_and_serialises() -> None:
    import json

    bill = sbom.from_lineage(
        model="cim-gbr",
        version="1.0",
        artefact_sha256="6" * 64,
        lineage_nodes=[item.as_payload() for item in chain().nodes],
        generated_at=AT,
        tools=[{"name": "draupnir", "version": "0.1.0"}],
    )

    assert bill.licences == ("Apache-2.0", "CC-BY-4.0")
    assert json.loads(bill.to_json())["metadata"]["tools"]["components"][0]["name"] == "draupnir"


def test_an_sbom_whose_subject_is_not_a_model_is_refused() -> None:
    with pytest.raises(sbom.SbomError, match="describes the wrong thing"):
        sbom.Sbom(
            subject=sbom.Component(name="corpus", type=sbom.DATA, sha256="1" * 64),
            components=(),
            generated_at=AT,
        )


def test_a_component_reference_comes_from_its_hash_not_its_name() -> None:
    """Two corpora with one name and different content are two components."""
    first = sbom.Component(name="corpus", type=sbom.DATA, sha256="1" * 64)
    second = sbom.Component(name="corpus", type=sbom.DATA, sha256="2" * 64)

    assert first.bom_ref() != second.bom_ref()


def test_hashing_a_single_file_matches_its_content(tmp_path) -> None:  # type: ignore[no-untyped-def]
    import hashlib

    target = tmp_path / "weights.safetensors"
    target.write_bytes(b"weights")

    assert publish.hash_file(target) == hashlib.sha256(b"weights").hexdigest()


def test_verifying_formats_with_none_built_is_refused() -> None:
    from draupnir.core.domain.evidence import EvidenceLog

    with pytest.raises(publish.PublicationError, match="nothing to verify"):
        publish.verify_formats(EvidenceLog(), built_formats=[])


def test_a_card_section_renders_a_missing_boolean_as_an_absence() -> None:
    card = modelcard.render(
        model="cim-gbr",
        generated_at=AT,
        identity={},
        provenance={},
        evaluation={},
        compliance={},
    )

    assert not card.complete
    assert len(card.unrecorded) == len(
        modelcard.IDENTITY_FIELDS
        + modelcard.PROVENANCE_FIELDS
        + modelcard.EVALUATION_FIELDS
        + modelcard.COMPLIANCE_FIELDS
    )
    assert "not recorded" in card.to_markdown()
