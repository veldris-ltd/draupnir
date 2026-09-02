"""SKIDBLADNIR: lineage, Article 53, the model card, and publication.

AC-F10: a release produces a model card, a CycloneDX SBOM, a SHA-256 manifest
and a lineage attestation, all four present and internally consistent.

AC-F11: the lineage endpoint returns the complete chain to base model licences
and corpus hashes with no gaps.

AC-F17: the release package contains the Article 53 training content summary
and the copyright policy reference, both generated from the licence register
and neither hand authored.

AC-S8: modifying an artefact after its gates pass causes publication to fail on
hash re-verification. Tested here against real bytes on disk, because the
control is "re-hash what is about to be published" and a test that passed a
hash in would be testing a lookup.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from draupnir.core.domain.evidence import (
    ArtefactMismatchError,
    Evidence,
    EvidenceLog,
    UngatedArtefactError,
)
from draupnir.gleipnir.copyright import for_release
from draupnir.hodd.register import LicenceRegister, SourceRecord
from draupnir.skidbladnir import article53, formats, lineage, modelcard, publish, sbom
from draupnir.skidbladnir.facts import NOT_RECORDED, Fact, FactError
from draupnir.skidbladnir.formats import DivergenceError
from draupnir.skidbladnir.lineage import IncompleteLineageError, Node
from draupnir.skidbladnir.publish import (
    IncompletePackageError,
    PublicationError,
    UnapprovedReleaseError,
)

AT = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)

SOURCE = "1" * 64
SOURCE_2 = "7" * 64
CORPUS = "2" * 64
BASE_MODEL = "3" * 64
ADAPTER = "4" * 64
MERGED = "5" * 64
RELEASE = "6" * 64


def nodes() -> tuple[Node, ...]:
    """A complete chain: release <- merged <- adapter <- corpus <- source, and base."""
    return (
        Node(sha256=SOURCE, kind="source", licence="CC-BY-4.0", label="hansard"),
        Node(sha256=SOURCE_2, kind="source", licence="OGL-UK-3.0", label="legislation"),
        Node(
            sha256=CORPUS,
            kind="corpus_curated",
            derived_from=(SOURCE, SOURCE_2),
            label="GBR curated",
        ),
        Node(
            sha256=BASE_MODEL,
            kind="base_model",
            licence="Apache-2.0",
            label="MIDGARD-CORE-GEMMA3-27B",
        ),
        Node(sha256=ADAPTER, kind="adapter", derived_from=(CORPUS, BASE_MODEL)),
        Node(sha256=MERGED, kind="merged", derived_from=(ADAPTER, BASE_MODEL)),
        Node(sha256=RELEASE, kind="quantised", derived_from=(MERGED,), label="cim-gbr nvfp4"),
    )


def register() -> LicenceRegister:
    """Two sources, one of them carrying an attribution obligation."""
    return LicenceRegister(
        [
            SourceRecord(
                id=UUID(int=1),
                jurisdiction="GBR",
                url="https://hansard.parliament.uk",
                licence_spdx="CC-BY-4.0",
                attribution_required=True,
                retrieved_at=AT,
                sha256=SOURCE,
                personal_data=False,
            ),
            SourceRecord(
                id=UUID(int=2),
                jurisdiction="GBR",
                url="https://legislation.gov.uk",
                licence_spdx="OGL-UK-3.0",
                attribution_required=False,
                retrieved_at=AT,
                sha256=CORPUS,
                personal_data=False,
            ),
        ]
    )


# ---------------------------------------------------------------------------
# AC-F11: lineage
# ---------------------------------------------------------------------------


def test_the_chain_reaches_base_model_licences_and_corpus_hashes() -> None:
    """AC-F11, in full."""
    chain = lineage.build(RELEASE, nodes())

    assert chain.complete
    assert chain.gaps() == ()
    assert chain.licences() == ("Apache-2.0", "CC-BY-4.0", "OGL-UK-3.0")
    assert chain.corpus_licences() == ("CC-BY-4.0", "OGL-UK-3.0")
    assert SOURCE in chain.corpus_hashes()
    assert CORPUS in chain.corpus_hashes()


def test_a_missing_link_is_reported_as_a_gap() -> None:
    """A structure that cannot express a gap always satisfies "no gaps"."""
    broken = tuple(item for item in nodes() if item.sha256 != CORPUS)

    chain = lineage.build(RELEASE, broken)

    assert not chain.complete
    assert any("missing" in gap for gap in chain.gaps())


def test_a_chain_that_stops_short_of_a_licence_is_a_gap() -> None:
    """A curated corpus with no source looks complete until counsel asks."""
    orphaned = (
        Node(sha256=CORPUS, kind="corpus_curated"),
        Node(sha256=BASE_MODEL, kind="base_model", licence="Apache-2.0"),
        Node(sha256=RELEASE, kind="quantised", derived_from=(CORPUS, BASE_MODEL)),
    )

    chain = lineage.build(RELEASE, orphaned)

    assert not chain.complete
    assert any("is not a root" in gap for gap in chain.gaps())


def test_a_root_with_no_licence_is_refused() -> None:
    with pytest.raises(lineage.LineageError, match="must carry a licence"):
        Node(sha256=SOURCE, kind="source")


def test_attestation_refuses_a_chain_with_gaps() -> None:
    """Publishing an attestation over a broken chain asserts an unfollowable claim."""
    broken = lineage.build(RELEASE, tuple(item for item in nodes() if item.sha256 != CORPUS))

    with pytest.raises(IncompleteLineageError, match="provenance nobody can follow"):
        lineage.attest(broken, attested_at=AT)


def test_lineage_survives_a_corpus_deleted_under_retention() -> None:
    """AC-F19's other half: the record outlives the bytes (SAD 7.3)."""
    retained = tuple(
        Node(
            sha256=item.sha256,
            kind=item.kind,
            derived_from=item.derived_from,
            licence=item.licence,
            label=item.label,
            bytes_retained=item.sha256 != CORPUS,
        )
        for item in nodes()
    )

    chain = lineage.build(RELEASE, retained)

    assert chain.complete
    assert [item.sha256 for item in chain.deleted_under_retention()] == [CORPUS]


def test_a_node_nothing_points_at_is_not_part_of_the_provenance() -> None:
    """Including it would overstate what the chain shows."""
    unrelated = Node(sha256="9" * 64, kind="source", licence="MIT")

    chain = lineage.build(RELEASE, (*nodes(), unrelated))

    assert "MIT" not in chain.licences()


def test_the_attestation_carries_the_approval_and_its_digest() -> None:
    """SAD 9.4: the sole approver exception is visible in the lineage."""
    attestation = lineage.attest(
        lineage.build(RELEASE, nodes()),
        attested_at=AT,
        approval={"approver": "akuma", "soleApproverException": True},
        signature="sig",
    )

    payload = attestation.as_payload()
    assert payload["approval"]["soleApproverException"] is True
    assert len(payload["digest"]) == 64


# ---------------------------------------------------------------------------
# AC-F17: Article 53
# ---------------------------------------------------------------------------


def summary() -> article53.TrainingContentSummary:
    """The training content summary, generated from the register."""
    return article53.summarise(
        model="cim-gbr-v1.0",
        licence_facts=register().facts_for_policy(),
        generated_at=AT,
        copyright_policy=for_release("gleipnir-licence/2026.01", AT).reference("hodd://sindri"),
    )


def test_the_summary_is_generated_from_the_licence_register() -> None:
    """AC-F17: generated, not authored."""
    rendered = summary()

    assert rendered.total_sources == 2
    assert rendered.licences == ("CC-BY-4.0", "OGL-UK-3.0")
    assert rendered.jurisdictions == ("GBR",)
    assert rendered.attribution_required == ("CC-BY-4.0",)


def test_the_summary_records_the_template_version_in_force() -> None:
    """SAD 10.2: a published release keeps the version in force at its date."""
    assert summary().template_version == article53.TEMPLATE_VERSION
    assert summary().as_payload()["templateVersion"] == article53.TEMPLATE_VERSION


def test_the_summary_references_the_copyright_policy_by_version() -> None:
    """AC-F17's second half. Referenced, not restated."""
    payload = summary().as_payload()

    assert payload["general_information"]["copyrightPolicy"]["version"] == "copyright/2026.01"


def test_the_summary_renders_the_three_template_sections() -> None:
    payload = summary().as_payload()

    for section in article53.SECTIONS:
        assert section in payload


def test_a_source_with_no_licence_is_stated_rather_than_dropped() -> None:
    """Decision S11: no template with blanks for somebody to fill in."""
    rendered = article53.summarise(
        model="cim-gbr-v1.0",
        licence_facts=[{"jurisdiction": "GBR"}],
        generated_at=AT,
        copyright_policy={},
    )

    assert rendered.absences == ("source[0].licenceSpdx",)
    assert rendered.as_payload()["notRecorded"] == ["source[0].licenceSpdx"]


def test_an_empty_register_is_refused() -> None:
    """A summary over no sources asserts a model trained on nothing."""
    with pytest.raises(article53.Article53Error, match="trained on nothing"):
        article53.summarise(
            model="cim-gbr-v1.0", licence_facts=[], generated_at=AT, copyright_policy={}
        )


def test_the_downstream_annex_places_article_50_with_the_suite() -> None:
    """SAD 9A.1: Article 50 is the Midgard Suite's, not the forge's."""
    payload = article53.annex(model="cim-gbr-v1.0", generated_at=AT, summary=summary()).as_payload()

    boundary = payload["regulatoryBoundary"]
    assert "not discharged here" in boundary["article50"]
    assert "downstream provider" in boundary["article50"]
    assert payload["attributionObligations"] == ["CC-BY-4.0"]


def test_the_annex_states_that_systemic_risk_does_not_attach() -> None:
    """SAD 9A.1: CIM-56 is orders of magnitude below the threshold."""
    payload = article53.annex(model="m", generated_at=AT, summary=summary()).as_payload()

    assert "Article 55 obligations do not attach" in payload["regulatoryBoundary"]["systemicRisk"]


def test_this_package_implements_no_watermarking() -> None:
    """The prompt forbids it: Article 50 belongs to the Midgard Suite.

    Checked against what the code *defines and imports*, not against what it
    says. The modules discuss Article 50 at length in order to place it with the
    Suite, and that prose is the point rather than a violation of it. What would
    be a violation is a function, a class, a constant or an import that does the
    marking.
    """
    import ast

    import draupnir.skidbladnir as package

    banned = ("watermark", "synthid", "c2pa", "contentcredential", "provenancemark")
    offences: list[str] = []

    for path in sorted(Path(package.__file__).parent.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.append(node.name)
            elif isinstance(node, ast.Name):
                names.append(node.id)
            elif isinstance(node, ast.Attribute):
                names.append(node.attr)
            elif isinstance(node, ast.alias):
                names.append(node.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module)

        for name in names:
            flattened = name.lower().replace("_", "").replace(".", "")
            offences += [f"{path.name}:{name}" for word in banned if word in flattened]

    assert not offences, (
        f"{', '.join(offences)} implements Article 50 marking. Article 50 attaches "
        "to the system that generates content and to its provider and deployer -- "
        "that is the Midgard Suite, not the forge. DRAUPNIR discharges Article 53 "
        "and must not absorb duties belonging to the Suite (SAD 9A.1)."
    )


def test_the_article_53_module_says_where_article_50_belongs() -> None:
    """The boundary is stated, because an unstated boundary gets crossed."""
    assert "Article 50 is not here and must not be added here" in (article53.__doc__ or "")


# ---------------------------------------------------------------------------
# The model card
# ---------------------------------------------------------------------------


def card(**overrides: object) -> modelcard.ModelCard:
    """A card with every fact recorded unless a test removes one."""
    identity: dict[str, Any] = {
        "model": "cim-gbr-v1.0",
        "jurisdiction": "GBR",
        "tier": "A",
        "version": "1.0",
        "artefactSha256": RELEASE,
        "route": "B",
        "formats": ["nvfp4", "gguf-q4km", "mlx4"],
    }
    provenance: dict[str, Any] = {
        "baseModel": BASE_MODEL,
        "baseModelLicence": "Apache-2.0",
        "runId": "run-1",
        "specificationHash": "d" * 64,
        "trainedAt": AT.isoformat(),
        "approver": "akuma",
        "approvedAt": AT.isoformat(),
        "soleApproverException": True,
    }
    evaluation: dict[str, Any] = {
        "suite": "general-core",
        "suiteVersion": "2026.01",
        "baselineSha256": BASE_MODEL,
        "gatesPassed": ["E1", "E2", "E3", "E4", "E5", "E6"],
        "regression": "none",
    }
    compliance: dict[str, Any] = {
        "trainingContentSummary": "hodd://sindri/releases/cim-gbr/summary.json",
        "copyrightPolicy": "hodd://sindri/policy/copyright.json",
        "copyrightPolicyVersion": "gleipnir-copyright/2026.01",
        "downstreamAnnex": "hodd://sindri/releases/cim-gbr/annex.json",
        "personalDataPresent": False,
        "attributionObligations": ["CC-BY-4.0"],
    }
    for key, value in overrides.items():
        for section in (identity, provenance, evaluation, compliance):
            if key not in section:
                continue
            if value is None:
                section.pop(key)
            else:
                section[key] = value
    return modelcard.render(
        model="cim-gbr-v1.0",
        generated_at=AT,
        identity=identity,
        provenance=provenance,
        evaluation=evaluation,
        compliance=compliance,
    )


def test_a_complete_card_records_every_field() -> None:
    assert card().complete
    assert card().unrecorded == ()


def test_an_absent_fact_is_stated_and_never_silently_omitted() -> None:
    """The requirement, exactly."""
    without = card(approver=None)

    assert not without.complete
    assert "provenance.approver" in without.unrecorded
    assert NOT_RECORDED in without.provenance.get("approver").render()
    assert "approver" in without.to_markdown()


def test_a_false_boolean_is_a_recorded_fact_not_an_absence() -> None:
    """A release where the approver did not also submit records `false`."""
    result = card(soleApproverException=False)

    assert result.complete
    assert result.provenance.get("soleApproverException").known
    assert result.provenance.get("soleApproverException").render() == "no"


def test_the_sole_approver_exception_appears_on_the_card() -> None:
    """SAD 9.4 and AC-S15: visible in the model card, not only in the lineage."""
    assert card().provenance.get("soleApproverException").render() == "yes"


def test_a_fact_with_a_value_and_no_source_is_refused() -> None:
    """Decision S11: a value with no provenance cannot be checked."""
    with pytest.raises(FactError, match="records no source"):
        Fact(name="approver", value="akuma", source="")


def test_an_absence_with_no_reason_is_refused() -> None:
    with pytest.raises(FactError, match="not an explanation"):
        Fact(name="approver", known=False)


def test_the_markdown_card_shows_the_absences_at_the_top() -> None:
    text = card(runId=None).to_markdown()

    assert "not recorded" in text
    assert "provenance.runId" in text


# ---------------------------------------------------------------------------
# The cross-platform quantisation check
# ---------------------------------------------------------------------------


def test_agreeing_builds_pass_the_cross_check() -> None:
    result = formats.check_mlx_against_nvfp4(
        nvfp4_sha256="7" * 64,
        nvfp4_measurements={"E1": 0.80, "E4": 0.71},
        mlx_sha256="8" * 64,
        mlx_measurements={"E1": 0.802, "E4": 0.709},
    )

    assert result.agrees
    assert result.cross_platform


def test_a_divergent_mlx_build_raises_rather_than_passing() -> None:
    """The requirement's words. Both builds may still clear their gates."""
    with pytest.raises(DivergenceError) as raised:
        formats.check_mlx_against_nvfp4(
            nvfp4_sha256="7" * 64,
            nvfp4_measurements={"E1": 0.80, "E4": 0.71},
            mlx_sha256="8" * 64,
            mlx_measurements={"E1": 0.74, "E4": 0.709},
        )

    assert "E1 differs by 0.0600" in str(raised.value)
    assert "one of the two conversion pipelines being wrong" in str(raised.value)


def test_the_threshold_is_tighter_than_the_tightest_gate_margin() -> None:
    """A gate asks if the model is good enough; this asks if they are one model."""
    from draupnir.gleipnir.gates import BY_ID, Comparison

    #: Relative gates only. E6 is an absolute ceiling of 0.001 on contamination,
    #: which is a different kind of number: it is not a tolerance for how far a
    #: measurement may move, so comparing against it would compare two things
    #: that only look alike because both are small.
    relative = {Comparison.NO_WORSE_THAN, Comparison.BETTER_THAN}
    tightest = min(gate.margin for gate in BY_ID.values() if gate.comparison in relative)

    assert tightest == 0.01
    assert tightest > formats.DIVERGENCE_THRESHOLD


def test_builds_measured_on_different_things_are_refused() -> None:
    """Comparing on the intersection would narrow the check invisibly."""
    with pytest.raises(formats.FormatError, match="different things"):
        formats.cross_check(
            reference_format="nvfp4",
            candidate_format="mlx4",
            reference_sha256="7" * 64,
            candidate_sha256="8" * 64,
            reference_measurements={"E1": 0.8, "E4": 0.7},
            candidate_measurements={"E1": 0.8},
        )


def test_mlx_is_built_on_alviss_and_nvfp4_on_the_forge() -> None:
    assert formats.BUILD_HOST[formats.Format.MLX4] == "alviss"
    assert formats.BUILD_HOST[formats.Format.NVFP4] == "sindri"


# ---------------------------------------------------------------------------
# AC-F10 and AC-S8: the release package and publication
# ---------------------------------------------------------------------------


def package(artefact_sha256: str = RELEASE) -> publish.ReleasePackage:
    """A consistent release package for `artefact_sha256`."""
    chain = lineage.build(artefact_sha256, _nodes_rooted_at(artefact_sha256))
    rendered = summary()
    return publish.ReleasePackage(
        model="cim-gbr-v1.0",
        artefact_sha256=artefact_sha256,
        card=card(artefactSha256=artefact_sha256),
        sbom=sbom.from_lineage(
            model="cim-gbr-v1.0",
            version="1.0",
            artefact_sha256=artefact_sha256,
            lineage_nodes=[item.as_payload() for item in chain.nodes],
            generated_at=AT,
        ),
        attestation=lineage.attest(
            chain, attested_at=AT, approval={"approver": "akuma"}, signature="sig"
        ),
        summary=rendered,
        annex=article53.annex(model="cim-gbr-v1.0", generated_at=AT, summary=rendered),
        template_version=article53.TEMPLATE_VERSION,
        copyright_policy_version="gleipnir-copyright/2026.01",
    )


def _nodes_rooted_at(sha256: str) -> tuple[Node, ...]:
    """The standard chain, with the release node given a different hash."""
    return tuple(
        Node(
            sha256=sha256 if item.sha256 == RELEASE else item.sha256,
            kind=item.kind,
            derived_from=item.derived_from,
            licence=item.licence,
            label=item.label,
        )
        for item in nodes()
    )


def built(tmp_path: Path) -> tuple[Path, str]:
    """Write a model directory and return it with its tree hash."""
    root = tmp_path / "release"
    root.mkdir()
    (root / "model-nvfp4.safetensors").write_bytes(b"weights")
    (root / "config.json").write_text(json.dumps({"a": 1}), encoding="utf-8")
    return root, publish.hash_tree(root)


def log_for(sha256: str, *, formats_built: tuple[str, ...] = ("nvfp4",)) -> EvidenceLog:
    """Passing evidence for the release and for each built format."""
    log = EvidenceLog().with_evidence(
        Evidence(
            artefact_sha256=sha256,
            artefact_kind="quantised",
            outcomes=(),
            passed=True,
            suite="general-core",
            suite_version="2026.01",
            evaluated_at=AT,
            format=formats_built[0],
        )
    )
    for index, name in enumerate(formats_built[1:], start=1):
        log = log.with_evidence(
            Evidence(
                artefact_sha256=f"{index}{sha256[1:]}",
                artefact_kind="quantised",
                outcomes=(),
                passed=True,
                suite="general-core",
                suite_version="2026.01",
                evaluated_at=AT,
                format=name,
            )
        )
    return log


APPROVAL = {"decision": "approved", "signature": "sig", "approver": "akuma"}


def test_a_release_produces_four_internally_consistent_artefacts(tmp_path: Path) -> None:
    """AC-F10."""
    root, sha = built(tmp_path)

    result = publish.publish(
        package(sha),
        artefact=root,
        evidence_log=log_for(sha),
        approval={**APPROVAL, "artefactSha256": sha},
        released_at=AT,
        built_formats=["nvfp4"],
    )

    manifest = result.manifest["artefacts"]
    assert set(manifest) == {
        "modelCard",
        "sbom",
        "lineageAttestation",
        "trainingContentSummary",
    }
    assert all(len(digest) == 64 for digest in manifest.values())


def test_the_manifest_records_the_article_53_versions(tmp_path: Path) -> None:
    """AC-F17: template version and copyright policy version, on the release."""
    root, sha = built(tmp_path)

    result = publish.publish(
        package(sha),
        artefact=root,
        evidence_log=log_for(sha),
        approval=APPROVAL,
        released_at=AT,
        built_formats=["nvfp4"],
    )

    article = result.manifest["article53"]
    assert article["templateVersion"] == article53.TEMPLATE_VERSION
    assert article["copyrightPolicyVersion"] == "gleipnir-copyright/2026.01"


def test_modifying_the_artefact_after_its_gates_pass_fails_publication(
    tmp_path: Path,
) -> None:
    """AC-S8, against real bytes. The hash is computed, not looked up."""
    root, sha = built(tmp_path)
    evidence_log = log_for(sha)
    release = package(sha)

    # Tamper: one byte, after the gates passed.
    (root / "model-nvfp4.safetensors").write_bytes(b"weightz")

    with pytest.raises(ArtefactMismatchError) as raised:
        publish.publish(
            release,
            artefact=root,
            evidence_log=evidence_log,
            approval=APPROVAL,
            released_at=AT,
            built_formats=["nvfp4"],
        )

    assert raised.value.expected == sha
    assert raised.value.observed != sha


def test_an_artefact_with_no_evidence_at_all_is_refused(tmp_path: Path) -> None:
    """An absence means a stage was skipped, not that something changed."""
    root, sha = built(tmp_path)

    with pytest.raises(UngatedArtefactError, match="bypassed evaluation"):
        publish.publish(
            package(sha),
            artefact=root,
            evidence_log=EvidenceLog(),
            approval=APPROVAL,
            released_at=AT,
            built_formats=["nvfp4"],
        )


def test_a_format_built_but_never_regated_blocks_publication(tmp_path: Path) -> None:
    """AC-F9: there is no path from quantisation to approval that skips evaluation."""
    root, sha = built(tmp_path)

    with pytest.raises(PublicationError, match="never evaluated: mlx4"):
        publish.publish(
            package(sha),
            artefact=root,
            evidence_log=log_for(sha),
            approval=APPROVAL,
            released_at=AT,
            built_formats=["nvfp4", "mlx4"],
        )


def test_publication_without_an_approval_is_refused(tmp_path: Path) -> None:
    """SAD 5.2: SKIDBLADNIR must not publish without a GLEIPNIR approval."""
    root, sha = built(tmp_path)

    with pytest.raises(UnapprovedReleaseError, match="no approval record"):
        publish.publish(
            package(sha),
            artefact=root,
            evidence_log=log_for(sha),
            approval=None,
            released_at=AT,
            built_formats=["nvfp4"],
        )


def test_publication_with_an_unsigned_approval_is_refused(tmp_path: Path) -> None:
    root, sha = built(tmp_path)

    with pytest.raises(UnapprovedReleaseError, match="no signature"):
        publish.publish(
            package(sha),
            artefact=root,
            evidence_log=log_for(sha),
            approval={"decision": "approved"},
            released_at=AT,
            built_formats=["nvfp4"],
        )


def test_an_approval_naming_different_bytes_is_refused(tmp_path: Path) -> None:
    """An approval that names only a run survives the artefact being rebuilt."""
    root, sha = built(tmp_path)

    with pytest.raises(ArtefactMismatchError):
        publish.publish(
            package(sha),
            artefact=root,
            evidence_log=log_for(sha),
            approval={**APPROVAL, "artefactSha256": "f" * 64},
            released_at=AT,
            built_formats=["nvfp4"],
        )


def test_an_sbom_describing_a_different_artefact_is_caught(tmp_path: Path) -> None:
    """AC-F10's "internally consistent", checked rather than assumed."""
    import dataclasses

    root, sha = built(tmp_path)
    inconsistent = dataclasses.replace(package(sha), card=card(artefactSha256="f" * 64))

    problems = inconsistent.consistency_problems()

    assert any("model card names" in problem for problem in problems)
    with pytest.raises(IncompletePackageError):
        publish.publish(
            inconsistent,
            artefact=root,
            evidence_log=log_for(sha),
            approval=APPROVAL,
            released_at=AT,
            built_formats=["nvfp4"],
        )


def test_a_stale_summary_missing_a_licence_is_caught(tmp_path: Path) -> None:
    """The summary and the lineage are two renderings of the same corpus."""
    import dataclasses

    _, sha = built(tmp_path)
    thin = article53.summarise(
        model="cim-gbr-v1.0",
        licence_facts=[
            {"licenceSpdx": "CC-BY-4.0", "jurisdiction": "GBR", "attributionRequired": True}
        ],
        generated_at=AT,
        copyright_policy={},
    )
    inconsistent = dataclasses.replace(package(sha), summary=thin)

    problems = inconsistent.consistency_problems()

    assert any(
        "omits corpus licence" in problem and "OGL-UK-3.0" in problem for problem in problems
    ), problems
    # And the base model's licence is *not* reported: it is a component, not
    # training content, so a correct summary must not be called stale for it.
    assert not any("Apache-2.0" in problem for problem in problems)


def test_the_sbom_keeps_a_corpus_deleted_under_retention() -> None:
    """Removing the row would make the release look trained on less than it was."""
    chain = lineage.build(
        RELEASE,
        tuple(
            Node(
                sha256=item.sha256,
                kind=item.kind,
                derived_from=item.derived_from,
                licence=item.licence,
                label=item.label,
                bytes_retained=item.sha256 != CORPUS,
            )
            for item in nodes()
        ),
    )

    bill = sbom.from_lineage(
        model="cim-gbr-v1.0",
        version="1.0",
        artefact_sha256=RELEASE,
        lineage_nodes=[item.as_payload() for item in chain.nodes],
        generated_at=AT,
    )

    deleted = next(item for item in bill.components if item.sha256 == CORPUS)
    assert "deleted under the retention policy" in deleted.description


def test_the_sbom_is_cyclonedx_and_names_the_model_as_its_subject() -> None:
    bill = package().sbom.as_payload()

    assert bill["bomFormat"] == "CycloneDX"
    assert bill["specVersion"] == sbom.SPEC_VERSION
    assert bill["metadata"]["component"]["type"] == sbom.MODEL


def test_hashing_a_tree_notices_a_renamed_shard(tmp_path: Path) -> None:
    """Renaming shards changes which weights load, so the hash must change."""
    root = tmp_path / "model"
    root.mkdir()
    (root / "shard-0.safetensors").write_bytes(b"one")
    (root / "shard-1.safetensors").write_bytes(b"two")
    before = publish.hash_tree(root)

    (root / "shard-0.safetensors").rename(root / "shard-2.safetensors")

    assert publish.hash_tree(root) != before


def test_publishing_an_empty_directory_is_refused(tmp_path: Path) -> None:
    empty = tmp_path / "nothing"
    empty.mkdir()

    with pytest.raises(PublicationError, match="nothing to publish"):
        publish.hash_tree(empty)
