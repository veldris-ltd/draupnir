"""Answer "may this artefact be released?" before anyone signs anything.

Every refusal here is the real one. `refusals()` calls `skidbladnir.publish`
and reports what it raised, rather than re-implementing the checks: a preflight
that agreed with its own copy of the rules would pass a release the release
path then refuses, which is the worst possible time to find out.

Two checks are added, because `publish` cannot see them:

  - the site's anchor state, since a partitioned site continues to train and
    cannot release (Decision S8);
  - approver against submitter, since no role both submits and approves
    (Decision S6), and the exception is computed rather than accepted.

`--demo` builds a complete release from the real modules -- real bytes on disk,
a real SHA-256, a real lineage, a real Article 53 summary generated from a
licence register -- and reports it, then breaks it four ways and reports each
refusal. `tests/contract/test_skills.py` runs exactly that.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from draupnir.core.domain.evidence import Evidence, EvidenceError, EvidenceLog
from draupnir.gleipnir.copyright import for_release
from draupnir.hodd.register import LicenceRegister, SourceRecord
from draupnir.interfaces.types import GateOutcome
from draupnir.skidbladnir import article53, lineage, modelcard, publish, sbom
from draupnir.skidbladnir.lineage import Node
from draupnir.skidbladnir.publish import PublicationError, ReleasePackage, hash_file

#: A partitioned site trains and does not release (Decision S8). Anchored and
#: degraded both release; only a partition blocks it, and the difference is
#: exactly the point -- degraded is slow, partitioned is cut off.
RELEASABLE_ANCHOR_STATES = frozenset({"ANCHORED", "DEGRADED"})

#: The six gates of SAD 6.2. Named here so the demo's evidence is the evidence
#: a real release carries rather than a plausible-looking subset.
GATES = ("E1", "E2", "E3", "E4", "E5", "E6")


@dataclass(frozen=True, slots=True)
class Release:
    """Everything a preflight needs to look at."""

    package: ReleasePackage
    artefact: Path
    evidence: EvidenceLog
    approval: Mapping[str, Any] | None
    built_formats: tuple[str, ...]
    submitter: str
    anchor_state: str = "ANCHORED"


def refusals(release: Release, *, released_at: datetime | None = None) -> list[str]:
    """Every reason this release would be refused. Empty means it would go.

    A list rather than the first refusal: an operator holding a release wants
    to know everything that is wrong with it, not to discover the next problem
    after fixing this one and waiting for another allocation.
    """
    problems: list[str] = []

    if release.anchor_state not in RELEASABLE_ANCHOR_STATES:
        problems.append(
            f"the site is {release.anchor_state}. Training continues through a "
            "partition and release does not (Decision S8): a release signed from a "
            "site that cannot reach the federation cannot be anchored, and an "
            "unanchorable release is one nobody can later prove the date of."
        )

    approver = str((release.approval or {}).get("approver") or "")
    if approver and approver == release.submitter:
        problems.append(
            f"{approver} both submitted and approved this run. No role does both "
            "(Decision S6). The sole-approver exception is computed by GLEIPNIR from "
            "the two identities and is never accepted from a request, so suppressing "
            "it is a code review rather than a deployment."
        )

    try:
        publish.publish(
            release.package,
            artefact=release.artefact,
            evidence_log=release.evidence,
            approval=release.approval,
            released_at=released_at or datetime.now(UTC),
            built_formats=release.built_formats,
        )
    except (PublicationError, EvidenceError) as error:
        # Both, because the hash re-verification of AC-S8 raises an evidence
        # error rather than a publication one: what failed is that these bytes
        # have no passing evidence, which is a fact about the evidence log.
        problems.append(str(error))

    return problems


# ---------------------------------------------------------------------------
# The worked example
# ---------------------------------------------------------------------------

AT = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)
MODEL = "cim-gbr-v1.0"


def _digest_of(label: str) -> str:
    """A stable stand-in hash for an artefact this demo does not write."""
    import hashlib

    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def build_example(workdir: Path) -> Release:
    """A complete, releasable CIM-56 release, built from the real modules.

    The artefact is written to disk and hashed rather than given a made-up
    digest, because AC-S8's control is "re-hash what is about to be published"
    and an example that passed a hash in would be exercising a lookup.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    artefact = workdir / "cim-gbr-v1.0.nvfp4"
    artefact.write_bytes(b"not a model, but a stable sequence of bytes that hashes\n")
    released = hash_file(artefact)

    hansard = _digest_of("hansard")
    legislation = _digest_of("legislation")
    corpus = _digest_of("GBR curated")
    base = _digest_of("MIDGARD-CORE-GEMMA3-27B")
    adapter = _digest_of("cim-gbr adapter")
    merged = _digest_of("cim-gbr merged")

    nodes = (
        Node(sha256=hansard, kind="source", licence="CC-BY-4.0", label="hansard"),
        Node(sha256=legislation, kind="source", licence="OGL-UK-3.0", label="legislation"),
        Node(
            sha256=corpus,
            kind="corpus_curated",
            derived_from=(hansard, legislation),
            label="GBR curated",
        ),
        Node(
            sha256=base,
            kind="base_model",
            licence="Apache-2.0",
            label="MIDGARD-CORE-GEMMA3-27B",
        ),
        Node(sha256=adapter, kind="adapter", derived_from=(corpus, base)),
        Node(sha256=merged, kind="merged", derived_from=(adapter, base)),
        Node(sha256=released, kind="quantised", derived_from=(merged,), label="cim-gbr nvfp4"),
    )
    chain = lineage.build(released, nodes)

    register = LicenceRegister(
        [
            SourceRecord(
                id=UUID(int=1),
                jurisdiction="GBR",
                url="https://hansard.parliament.uk",
                licence_spdx="CC-BY-4.0",
                attribution_required=True,
                retrieved_at=AT,
                sha256=hansard,
                personal_data=False,
            ),
            SourceRecord(
                id=UUID(int=2),
                jurisdiction="GBR",
                url="https://legislation.gov.uk",
                licence_spdx="OGL-UK-3.0",
                attribution_required=False,
                retrieved_at=AT,
                sha256=legislation,
                personal_data=False,
            ),
        ]
    )

    # Generated from the register, never authored (AC-F17, Decision S11). The
    # summary and the licence decision are rendered from the same facts, so the
    # document and the decision cannot disagree.
    summary = article53.summarise(
        model=MODEL,
        licence_facts=register.facts_for_policy(),
        generated_at=AT,
        copyright_policy=for_release("gleipnir-licence/2026.01", AT).reference("hodd://sindri"),
    )

    package = ReleasePackage(
        model=MODEL,
        artefact_sha256=released,
        card=modelcard.render(
            model=MODEL,
            generated_at=AT,
            identity={
                "model": MODEL,
                "jurisdiction": "GBR",
                "tier": "A",
                "version": "1.0",
                "artefactSha256": released,
                "route": "B",
                "formats": ["nvfp4"],
            },
            provenance={
                "baseModel": base,
                "baseModelLicence": "Apache-2.0",
                "runId": "run-1",
                "specificationHash": _digest_of("spec"),
                "trainedAt": AT.isoformat(),
                "approver": "akuma",
                "approvedAt": AT.isoformat(),
                "soleApproverException": False,
            },
            evaluation={
                "suite": "general-core",
                "suiteVersion": "2026.01",
                "baselineSha256": base,
                "gatesPassed": list(GATES),
                "regression": "none",
            },
            compliance={
                "trainingContentSummary": "hodd://sindri/releases/cim-gbr/summary.json",
                "copyrightPolicy": "hodd://sindri/policy/copyright.json",
                "copyrightPolicyVersion": "gleipnir-copyright/2026.01",
                "downstreamAnnex": "hodd://sindri/releases/cim-gbr/annex.json",
                "personalDataPresent": False,
                "attributionObligations": ["CC-BY-4.0"],
            },
        ),
        sbom=sbom.from_lineage(
            model=MODEL,
            version="1.0",
            artefact_sha256=released,
            lineage_nodes=[item.as_payload() for item in chain.nodes],
            generated_at=AT,
        ),
        attestation=lineage.attest(
            chain, attested_at=AT, approval={"approver": "akuma"}, signature="sig"
        ),
        summary=summary,
        annex=article53.annex(model=MODEL, generated_at=AT, summary=summary),
        template_version=article53.TEMPLATE_VERSION,
        copyright_policy_version="gleipnir-copyright/2026.01",
    )

    evidence = EvidenceLog().with_evidence(
        Evidence(
            artefact_sha256=released,
            artefact_kind="quantised",
            outcomes=tuple(
                GateOutcome(gate=gate, suite_version="2026.01", value=1.0, passed=True)
                for gate in GATES
            ),
            passed=True,
            suite="general-core",
            suite_version="2026.01",
            evaluated_at=AT,
            baseline_sha256=base,
            format="nvfp4",
        )
    )

    return Release(
        package=package,
        artefact=artefact,
        evidence=evidence,
        approval={
            "approver": "akuma",
            "decision": "approved",
            "signature": "sig",
            "artefactSha256": released,
        },
        built_formats=("nvfp4",),
        submitter="brokkr-operator",
        anchor_state="ANCHORED",
    )


def broken(release: Release, how: str) -> Release:
    """The worked example with one thing wrong, for each refusal worth showing."""
    from dataclasses import replace

    if how == "partitioned":
        return replace(release, anchor_state="PARTITIONED")
    if how == "sole-approver":
        return replace(release, submitter="akuma")
    if how == "unapproved":
        return replace(release, approval=None)
    if how == "tampered":
        # AC-S8: the bytes change after the gates passed. Nothing else moves.
        release.artefact.write_bytes(b"different bytes entirely\n")
        return release
    if how == "unevaluated-format":
        return replace(release, built_formats=("nvfp4", "mlx4"))
    msg = f"unknown breakage {how!r}"
    raise ValueError(msg)


BREAKAGES = ("partitioned", "sole-approver", "unapproved", "unevaluated-format", "tampered")


def main(argv: list[str] | None = None) -> int:
    """Run the demonstration, or check a release built elsewhere."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true", help="build the worked example and check it")
    parser.add_argument("--workdir", type=Path, help="where the demo writes its artefact")
    args = parser.parse_args(argv)

    if not args.demo:
        parser.error(
            "--demo is the only mode this script has. A real preflight runs inside the "
            "control plane, where the package, the evidence log and the approval "
            "already exist; this is here so the skill's claims are exercised in CI."
        )

    workdir = args.workdir or Path("build") / "cim-release-demo"
    release = build_example(workdir)

    found = refusals(release, released_at=AT)
    print(f"complete release: {len(found)} refusal(s)")
    for problem in found:
        print(f"  - {problem}")
    print(json.dumps(release.package.manifest(released_at=AT), indent=2))

    for how in BREAKAGES:
        # Rebuilt each time: `tampered` rewrites the artefact on disk, and a
        # demonstration whose cases depend on their order is a demonstration
        # that proves whatever it was run in.
        case = broken(build_example(workdir), how)
        problems = refusals(case, released_at=AT)
        print(f"\n{how}: {len(problems)} refusal(s)")
        for problem in problems:
            print(f"  - {problem}")
        if not problems:
            print("  UNREFUSED -- this is a defect in the preflight, not in the release")
            return 1

    return 0 if not found else 1


if __name__ == "__main__":
    raise SystemExit(main())
