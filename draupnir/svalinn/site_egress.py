"""Reconciling DRAUPNIR's allow list with the site router's. RF-E17.

Two allow lists govern the same traffic and neither is the authority, which
means neither is evidence. VLD-INF-SINDRI-001 Rev 3.3 section 10.5 enforces
eighteen hosts at the site router; `egress.ALLOW_LIST` declares what the
control plane may reach. A destination in one and not the other fails
somewhere, and where it fails determines how it looks:

- **Permitted by the router, not declared here** — the broker refuses it. The
  call never leaves ALVISS and the log line says "not in the allow list", which
  is at least legible.
- **Declared here, not permitted by the router** — the packet leaves and is
  dropped. The failure surfaces as a timeout in whatever library made the
  request, and looks like the internet being slow.

The second is the dangerous one, and it is the one the reconciliation below
finds, because a `requests` timeout is nobody's idea of a policy decision.

**This module is not the router's configuration and does not generate it.**
That was the first design and it is wrong: the two lists are not the same list.
Section 10.5 governs everything on the estate, including the appliances' apt
and pip traffic during commissioning, which DRAUPNIR never makes. And this list
holds internal destinations -- REGIN, MEGINGJORD -- which are on the site
fabrics or over the federation link and never reach the router at all. Putting
them in an internet egress policy would widen it for no reason and misdescribe
where they are.

So what is generated is a *reconciliation*: for every host the programme
reaches, whether the router permits it, and for every host the router permits,
what needs it. Both directions, because an unclaimed router entry cannot be
pruned by anyone who does not know why it is there.

**On the transcription.** `SITE_ROUTER_POLICY` is section 10.5 copied out, with
its provenance recorded beside it. A test in the pipeline cannot read the
manual: it is a controlled document, it lives outside this repository, and it
is marked CONFIDENTIAL. So the test compares against this transcription, and
keeping the transcription honest is a step in the acceptance schedule rather
than something CI can do. That limit is stated here rather than left for
somebody to discover, because a transcription nobody re-checks is the same
failure this module exists to fix, one level down.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from draupnir.svalinn.egress import ALLOW_LIST, Destination

#: Where the transcription came from, so the next person can check it.
SOURCE_DOCUMENT: Final = "VLD-INF-SINDRI-001"
SOURCE_REVISION: Final = "3.3"
SOURCE_SECTION: Final = "10.5 Egress policy"
TRANSCRIBED_ON: Final = "2026-09-08"

#: Section 10.5, as written. Eighteen hosts -- the estate register said
#: seventeen, and it is eighteen.
#:
#: Order preserved from the document so a diff against a re-transcription is
#: readable rather than a reshuffle.
SITE_ROUTER_POLICY: Final[tuple[str, ...]] = (
    "github.com",
    "api.github.com",
    "codeload.github.com",
    "raw.githubusercontent.com",
    "huggingface.co",
    "cdn-lfs.huggingface.co",
    "pypi.org",
    "files.pythonhosted.org",
    "registry.npmjs.org",
    "archive.ubuntu.com",
    "security.ubuntu.com",
    "ports.ubuntu.com",
    "nvcr.io",
    "api.ngc.nvidia.com",
    "developer.download.nvidia.com",
    "registry-1.docker.io",
    "auth.docker.io",
    "production.cloudflare.docker.com",
)


#: The divergence that exists today, with why each entry is outstanding.
#:
#: A baseline rather than a target, and the distinction is what makes the gate
#: work. Nine hosts are reached by this programme and refused by section 10.5,
#: and not one of them can be fixed from this repository: the section is in a
#: controlled document. Gating on "nothing is blocked" would make the pipeline
#: red from the day it was added and therefore ignored, which is worse than not
#: gating -- a red build that is always red is not a signal.
#:
#: So the gate is on the *set*. A tenth blocked host fails the build, because
#: that is a new divergence somebody introduced. A blocked host disappearing
#: also fails it, because that means section 10.5 was amended and this
#: transcription needs re-taking. Either way the build asks a question that has
#: an answer.
KNOWN_BLOCKED: Final[dict[str, str]] = {
    # -- the estate cannot commission itself under its own policy -----------
    "astral.sh": (
        "Procedure S6 installs uv from it on all five machines, and Procedure S10 "
        "again on the Macs. Nothing in Part 3 or Part 4 completes without it."
    ),
    "download.pytorch.org": (
        "the PyTorch wheels. This is the training stack; the estate has no purpose "
        "without it, and the CUDA build is not on PyPI."
    ),
    "nvidia.github.io": (
        "the NVIDIA container toolkit signing key and apt source, which Procedure S5 "
        "installs before the DCGM exporter of section 34 step 6."
    ),
    # -- and cannot build the control plane it is meant to run --------------
    "ghcr.io": "the uv builder image in docker/api.Dockerfile.",
    "pkg-containers.githubusercontent.com": "where ghcr.io serves its layers from.",
    "gcr.io": "the distroless runtime image in docker/api.Dockerfile.",
    "storage.googleapis.com": "where gcr.io serves its layers from.",
    "cgr.dev": "the Chainguard node and nginx images in docker/web.Dockerfile.",
    # -- and cannot acquire the corpus it exists to train on ----------------
    "www.legislation.gov.uk": (
        "GBR primary legislation under the Open Government Licence v3.0, named in the "
        "Part 5 corpus manifest."
    ),
}


class Consumer(StrEnum):
    """Who in the programme makes a call to a host.

    The distinction the reconciliation turns on. Section 10.5 is the estate's
    policy and most of it has nothing to do with the control plane; a report
    that could not say so would read as though DRAUPNIR needed Ubuntu's
    archives.
    """

    #: The DRAUPNIR control plane, at run time. `egress.ALLOW_LIST`.
    CONTROL_PLANE = "control-plane"
    #: Building the DRAUPNIR images. `docker/`.
    IMAGE_BUILD = "image-build"
    #: Commissioning the appliances. VLD-INF-SINDRI-001 Part 3 and Part 4.
    COMMISSIONING = "commissioning"
    #: Acquiring corpus material. The manifests in Part 5.
    CORPUS = "corpus"


@dataclass(frozen=True, slots=True)
class Requirement:
    """One host something in the programme reaches, and what reaches it."""

    host: str
    consumer: Consumer
    purpose: str
    #: Where the call is written down, so the claim can be checked.
    citation: str

    def as_payload(self) -> dict[str, Any]:
        """The wire shape, for the generated reconciliation."""
        return {
            "host": self.host,
            "consumer": str(self.consumer),
            "purpose": self.purpose,
            "citation": self.citation,
        }


#: Hosts the programme reaches that are not the control plane's, and therefore
#: not in `egress.ALLOW_LIST`. Transcribed from the procedures that make the
#: call, each with the line that makes it.
#:
#: This exists because the reconciliation is only useful in both directions. A
#: report that listed section 10.5's Ubuntu archives as "permitted, unclaimed"
#: would be inviting somebody to prune the appliances' ability to patch.
OTHER_CONSUMERS: Final[tuple[Requirement, ...]] = (
    Requirement(
        host="astral.sh",
        consumer=Consumer.COMMISSIONING,
        purpose="the uv installer, on every appliance and on both Macs",
        citation="VLD-INF-SINDRI-001 Procedure S6 step 1: curl -LsSf https://astral.sh/uv/install.sh",
    ),
    Requirement(
        host="download.pytorch.org",
        consumer=Consumer.COMMISSIONING,
        purpose="the PyTorch wheels -- the training stack itself",
        citation=(
            "VLD-INF-SINDRI-001 Procedure S6 step 2: "
            "uv pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu130"
        ),
    ),
    Requirement(
        host="nvidia.github.io",
        consumer=Consumer.COMMISSIONING,
        purpose="the NVIDIA container toolkit signing key and apt source",
        citation="VLD-INF-SINDRI-001 Procedure S5: curl https://nvidia.github.io/libnvidia-container/gpgkey",
    ),
    Requirement(
        host="www.legislation.gov.uk",
        consumer=Consumer.CORPUS,
        purpose="GBR primary legislation, under the Open Government Licence v3.0",
        citation="VLD-INF-SINDRI-001 Part 5, the GBR corpus manifest",
    ),
    Requirement(
        host="github.com",
        consumer=Consumer.COMMISSIONING,
        purpose="LLaMA-Factory and the other training tools, cloned at commissioning",
        citation="VLD-INF-SINDRI-001 Procedure S6",
    ),
    Requirement(
        host="raw.githubusercontent.com",
        consumer=Consumer.COMMISSIONING,
        purpose="the Homebrew installer, on ALVISS and ANDVARI",
        citation="VLD-INF-SINDRI-001 Procedure S10 step 1",
    ),
    Requirement(
        host="archive.ubuntu.com",
        consumer=Consumer.COMMISSIONING,
        purpose="DGX OS package updates on the three appliances",
        citation="VLD-INF-SINDRI-001 Part 3, apt",
    ),
    Requirement(
        host="security.ubuntu.com",
        consumer=Consumer.COMMISSIONING,
        purpose="DGX OS security updates on the three appliances",
        citation="VLD-INF-SINDRI-001 Part 3, apt",
    ),
    Requirement(
        host="ports.ubuntu.com",
        consumer=Consumer.COMMISSIONING,
        purpose="the aarch64 package archive, which is where a DGX Spark's apt resolves",
        citation="VLD-INF-SINDRI-001 Part 3, apt on arm64",
    ),
)


class Verdict(StrEnum):
    """What the reconciliation concluded about one host."""

    #: Something reaches it and the router permits it. Nothing to do.
    RECONCILED = "reconciled"
    #: Something reaches it and the router does not permit it. The call leaves
    #: the host and is dropped, and it looks like a timeout.
    BLOCKED = "blocked-by-the-router"
    #: The router permits it and nothing in the programme claims it. Not a
    #: fault -- it may be a consumer nobody wrote down -- but an entry nobody
    #: can justify is an entry nobody can remove.
    UNCLAIMED = "permitted-but-unclaimed"
    #: Internal. The router is not in the path, so it has no opinion.
    NOT_ROUTED = "internal-to-the-site"


@dataclass(frozen=True, slots=True)
class Row:
    """One host, and where the two policies stand on it."""

    host: str
    verdict: Verdict
    consumers: tuple[Consumer, ...]
    purpose: str
    citation: str = ""
    gap: str = ""

    def as_payload(self) -> dict[str, Any]:
        """The wire shape."""
        return {
            "host": self.host,
            "verdict": str(self.verdict),
            "consumers": [str(item) for item in self.consumers],
            "purpose": self.purpose,
            "citation": self.citation,
            "gap": self.gap,
        }


def requirements_of(allow_list: Iterable[Destination] = ALLOW_LIST) -> tuple[Requirement, ...]:
    """The control plane's own destinations, as requirements.

    Derived rather than listed a second time. `egress.ALLOW_LIST` is the
    authority for what DRAUPNIR reaches, and a hand-kept copy here would be the
    same failure this module exists to fix.
    """
    return tuple(
        Requirement(
            host=item.host,
            consumer=Consumer.IMAGE_BUILD
            if item.approving_policy.startswith("supply-chain")
            else Consumer.CONTROL_PLANE,
            purpose=item.purpose,
            citation="draupnir/svalinn/egress.py ALLOW_LIST",
        )
        for item in allow_list
    )


def reconcile(
    allow_list: Iterable[Destination] = ALLOW_LIST,
    router_policy: Iterable[str] = SITE_ROUTER_POLICY,
    others: Iterable[Requirement] = OTHER_CONSUMERS,
) -> tuple[Row, ...]:
    """Compare the two policies, in both directions. One row per host."""
    destinations = tuple(allow_list)
    permitted = set(router_policy)
    internal = {item.host for item in destinations if not item.traverses_site_router}
    gaps = {item.host: item.gap for item in destinations if item.gap}

    wanted: dict[str, list[Requirement]] = {}
    for requirement in (*requirements_of(destinations), *others):
        wanted.setdefault(requirement.host, []).append(requirement)

    rows: list[Row] = []
    for host in sorted(wanted):
        claims = wanted[host]
        if host in internal:
            verdict = Verdict.NOT_ROUTED
        elif host in permitted:
            verdict = Verdict.RECONCILED
        else:
            verdict = Verdict.BLOCKED
        rows.append(
            Row(
                host=host,
                verdict=verdict,
                consumers=tuple(dict.fromkeys(item.consumer for item in claims)),
                purpose="; ".join(dict.fromkeys(item.purpose for item in claims)),
                citation="; ".join(dict.fromkeys(item.citation for item in claims)),
                gap=gaps.get(host, ""),
            )
        )

    for host in router_policy:
        if host not in wanted:
            rows.append(
                Row(
                    host=host,
                    verdict=Verdict.UNCLAIMED,
                    consumers=(),
                    purpose="nothing in this programme is recorded as reaching it",
                )
            )

    return tuple(rows)


def blocked(rows: Iterable[Row] | None = None) -> tuple[Row, ...]:
    """Every host something reaches and the router refuses. The finding."""
    return tuple(item for item in (rows or reconcile()) if item.verdict is Verdict.BLOCKED)


def unclaimed(rows: Iterable[Row] | None = None) -> tuple[Row, ...]:
    """Every router entry nothing here claims."""
    return tuple(item for item in (rows or reconcile()) if item.verdict is Verdict.UNCLAIMED)


def unrecorded(rows: Iterable[Row] | None = None) -> tuple[Row, ...]:
    """Blocked hosts that are not in the baseline. A new divergence."""
    return tuple(item for item in blocked(rows) if item.host not in KNOWN_BLOCKED)


def resolved(rows: Iterable[Row] | None = None) -> tuple[str, ...]:
    """Baseline entries that are no longer blocked.

    Not a result to be printed and forgotten: it means section 10.5 was
    amended, so the transcription above is a revision behind and every other
    conclusion drawn from it is suspect.
    """
    still = {item.host for item in blocked(rows)}
    return tuple(sorted(host for host in KNOWN_BLOCKED if host not in still))


def to_markdown(rows: Iterable[Row] | None = None) -> str:
    """The reconciliation, as the build artefact `docs/egress-policy.md`."""
    resolved = tuple(rows) if rows is not None else reconcile()
    lines = [
        "# Egress reconciliation",
        "",
        f"Generated from `draupnir/svalinn/egress.py` and the transcription of "
        f"{SOURCE_DOCUMENT} Rev {SOURCE_REVISION} section {SOURCE_SECTION} held in "
        "`draupnir/svalinn/site_egress.py`. Do not edit by hand.",
        "",
        f"The transcription was taken on {TRANSCRIBED_ON}. Nothing in the pipeline can "
        "verify it against the controlled document, which is why re-checking it is a "
        "step in the acceptance schedule rather than a test.",
        "",
    ]

    problems = [item for item in resolved if item.verdict is Verdict.BLOCKED]
    if problems:
        lines += [
            "## Blocked by the site router",
            "",
            "Something in the programme reaches these and section 10.5 does not permit "
            "them. The packet leaves the host and is dropped, so the failure presents as "
            "a timeout rather than as a policy decision.",
            "",
            "| Host | Needed by | Why | Where the call is written |",
            "|---|---|---|---|",
        ]
        lines += [
            f"| `{item.host}` | {', '.join(str(c) for c in item.consumers)} | "
            f"{item.purpose} | {item.citation} |"
            for item in problems
        ]
        lines.append("")

    lines += [
        "## Every host, and where the two policies stand",
        "",
        "| Host | Verdict | Needed by | Why |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| `{item.host}` | {item.verdict} | "
        f"{', '.join(str(c) for c in item.consumers) or '—'} | {item.purpose} |"
        for item in sorted(resolved, key=lambda row: (row.verdict, row.host))
    ]

    outstanding = [item for item in resolved if item.gap]
    if outstanding:
        lines += [
            "",
            "## Declared, but not reachable yet",
            "",
            "A permission for a path that does not exist. Worth having declared, and "
            "worth not mistaking for a live one.",
            "",
        ]
        lines += [f"- `{item.host}` — {item.gap}" for item in outstanding]

    lines += ["", "## Deliberately absent", ""]
    lines += [
        "`api.teacher-model.example` is not in either list and must not be. Distillation "
        "is out of scope for Release 1 (SAD Q3) and threat T3 is distillation-time "
        "exfiltration of corpus content. AC-S3 requires the destination to be absent and "
        "a call to it to fail with a logged refusal.",
        "",
    ]
    return "\n".join(lines) + "\n"


__all__ = [
    "KNOWN_BLOCKED",
    "OTHER_CONSUMERS",
    "SITE_ROUTER_POLICY",
    "SOURCE_DOCUMENT",
    "SOURCE_REVISION",
    "SOURCE_SECTION",
    "TRANSCRIBED_ON",
    "Consumer",
    "Requirement",
    "Row",
    "Verdict",
    "blocked",
    "reconcile",
    "requirements_of",
    "resolved",
    "to_markdown",
    "unclaimed",
    "unrecorded",
]
