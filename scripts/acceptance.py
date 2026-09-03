"""Assemble the acceptance evidence pack: one file per criterion in SAD 12.

The pack is the deliverable, not a summary written afterwards. Three things
follow from that.

**The criteria are parsed from the SAD**, not retyped here. A pack that
restated the criteria would eventually restate them wrongly, and the one place
that would not be checked is the place a reader checks first.

**The evidence is discovered**, not listed. Every criterion is cited in the
code that satisfies it -- `AC-F12` appears in the procedure runner, `AC-B10` in
the read model, `AC-U3` in the token linter -- so the pack finds those
citations and reports them. A test that moves file keeps its evidence; a
criterion that loses its last citation fails `--check`.

**The status is authored**, because it is the one thing no tool can derive.
IMPLEMENTED, DEVIATED with reasons, or NOT BUILT: the vocabulary AC-D4 asks
for, and the same vocabulary the Imhotep reconciliation uses.

    python scripts/acceptance.py            # write the pack
    python scripts/acceptance.py --check    # fail if it is stale or unbacked
"""

from __future__ import annotations

import argparse
import collections
import re
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAD = ROOT / "docs" / "build" / "draupnir-sad.md"
PACK = ROOT / "docs" / "acceptance"

#: Where a citation may live. `docs/build` is excluded: the SAD cites every
#: criterion by definition, and counting that as evidence would make the
#: specification its own proof.
SEARCHED = (
    "draupnir",
    "draupnirctl",
    "plugins",
    "scripts",
    "skills",
    "tests",
    "web",
    "migrations",
    ".github",
)

#: Root files that are evidence in their own right. The pipeline criteria are
#: satisfied by the workflow and the task runner, and a criterion whose only
#: home is a configuration file is not less demonstrated for that.
ROOT_FILES = ("tasks.py", "Makefile", ".importlinter", ".gitleaks.toml", "docker-compose.yml")
SUFFIXES = frozenset({".py", ".ts", ".tsx", ".mjs", ".yaml", ".yml", ".toml", ".sql"})
#: This file cites every criterion in SAD 12, because the register is here. It
#: must not count as evidence for any of them: a pack that cited itself would
#: report full coverage the moment it was written.
SELF = Path(__file__).name

SKIP = frozenset(
    {
        "node_modules",
        "dist",
        "dist-types",
        "storybook-static",
        "__pycache__",
        ".venv",
        "test-results",
        "playwright-report",
        "coverage",
    }
)

REFERENCE = re.compile(r"\bAC-[A-Z]\d+\b")
ROW = re.compile(r"^\|\s*(AC-[A-Z]+\d+)\s*\|(?P<rest>.+?)\|\s*$", re.MULTILINE)


class AcceptanceError(Exception):
    """Raised when the pack cannot be assembled or does not hold up."""


@dataclass(frozen=True, slots=True)
class Criterion:
    """One row of SAD 12, as the specification states it."""

    ref: str
    text: str
    priority: str


@dataclass(frozen=True, slots=True)
class Entry:
    """What this build claims about one criterion."""

    #: IMPLEMENTED, DEVIATED, or NOT BUILT. AC-D4's vocabulary.
    status: str
    #: How it is demonstrated, in one sentence.
    method: str
    #: What to run to see it. Empty where there is nothing to run.
    command: str = ""
    #: Why it deviates, or what is not built. Required for both of those.
    note: str = ""
    #: Set where the criterion is deliberately uncited in code.
    uncited: bool = False
    extra: tuple[str, ...] = field(default_factory=tuple)


IMPLEMENTED = "IMPLEMENTED"
DEVIATED = "DEVIATED"
NOT_BUILT = "NOT BUILT"

#: The one reason a criterion deviates in this build, written once. There is no
#: estate: no three appliances, no Slurm controller, no NFS vault, no GPU, no
#: uninterruptible supply, no WireGuard link to a federation registry. Anything
#: whose criterion is a measurement *on that hardware* cannot be measured here,
#: and saying so once is more honest than saying it ninety times differently.
NO_ESTATE = (
    "The Sindri estate does not exist yet: SAD 1.3 puts the hardware build in "
    "VLD-INF-SINDRI-001 and out of scope here. Everything the control plane owns is "
    "implemented and exercised; the measurement this criterion asks for is taken at "
    "commissioning, on the estate, and is not one a developer machine can stand in for."
)

REGISTER: dict[str, Entry] = {
    # -- 12.1 Functional ----------------------------------------------------
    "AC-F1": Entry(
        IMPLEMENTED,
        "Both clients post the specification and the API returns the identity it "
        "recorded, so agreement is a property of there being one implementation.",
        "make test-e2e",
    ),
    "AC-F2": Entry(
        IMPLEMENTED,
        "The orchestrator looks the identity up in the chain before registering, and "
        "reports the run that already carries it.",
        "make test-integration",
    ),
    "AC-F3": Entry(
        IMPLEMENTED,
        "M1 to M3 of the procedure ingest, hash, register and curate; the raw tree is "
        "made read only and a write to it is attempted and refused.",
        "make procedure",
    ),
    "AC-F4": Entry(
        DEVIATED,
        "Ring placement across three appliances is planned and refused when the estate "
        "is degraded; live step progress is parsed and streamed.",
        "make test-unit",
        note=NO_ESTATE + " No substrate run has executed on three appliances.",
    ),
    "AC-F5": Entry(
        DEVIATED,
        "A fifty six element array is built as one plan and capped at one element per "
        "available appliance.",
        "make test-unit",
        note=NO_ESTATE + " No array has executed on three appliances.",
    ),
    "AC-F6": Entry(
        IMPLEMENTED,
        "One element is retried by index without touching the others, and the retry "
        "budget is spent per element.",
        "make test-unit",
    ),
    "AC-F7": Entry(
        IMPLEMENTED,
        "The six gates are judged against a baseline with margins recorded, and a "
        "failure within budget requeues rather than failing the run.",
        "make test-unit",
    ),
    "AC-F8": Entry(
        IMPLEMENTED,
        "A five point sweep is built, judged and compared as one object; fewer than "
        "five points is refused.",
        "make test-unit",
    ),
    "AC-F9": Entry(
        DEVIATED,
        "Three formats are built and every one is re-gated before AWAITING_APPROVAL; "
        "publication refuses a format that was never evaluated.",
        "make procedure",
        note="The quantisers themselves run on the appliance. " + NO_ESTATE,
    ),
    "AC-F10": Entry(
        IMPLEMENTED,
        "The four artefacts are assembled from one lineage and checked against each "
        "other before publication; the manifest hashes all four.",
        "make test-unit",
    ),
    "AC-F11": Entry(
        IMPLEMENTED,
        "The chain reaches base model licences and corpus hashes, and an attestation "
        "over a chain with gaps is refused rather than signed.",
        "make test-unit",
    ),
    "AC-F12": Entry(
        IMPLEMENTED,
        "One command runs all ten steps against a real database, a real scheduler and "
        "real files, and the run ends RELEASED.",
        "make procedure",
    ),
    "AC-F13": Entry(
        IMPLEMENTED,
        "Cancelling stops the scheduler job and returns the state it actually reached; "
        "cancelling a finished job is not an error.",
        "make test-contract",
    ),
    "AC-F14": Entry(
        IMPLEMENTED,
        "A dry run validates and renders the plan without submitting; the console makes "
        "it the primary action.",
        "make test-e2e",
    ),
    "AC-F15": Entry(
        IMPLEMENTED,
        "Two jurisdictions are compiled through the same code path and only the dataset "
        "block differs.",
        "make test-integration",
    ),
    "AC-F16": Entry(
        IMPLEMENTED,
        "The nine and the forty seven are one table; the union is validated at "
        "submission and a duplicate or omission fails.",
        "make test-unit",
    ),
    "AC-F17": Entry(
        IMPLEMENTED,
        "The summary is rendered from the licence register's facts and the copyright "
        "policy is referenced by version and digest.",
        "make test-unit",
    ),
    "AC-F18": Entry(
        IMPLEMENTED,
        "A second site is seeded and every read is scoped; row level security returns "
        "zero rows to a session that has not chosen one.",
        "make test-integration",
    ),
    "AC-F19": Entry(
        IMPLEMENTED,
        "A retention action deletes the raw corpus and keeps the curated manifests and "
        "licence entries; the lineage stays complete.",
        "make test-unit",
    ),
    "AC-F20": Entry(
        IMPLEMENTED,
        "A retention action that would break a chain is refused, naming the release it "
        "would orphan.",
        "make test-unit",
    ),
    # -- 12.2 Security ------------------------------------------------------
    "AC-S1": Entry(
        IMPLEMENTED,
        "Loading re-hashes the artefact and refuses a mismatch, recording it; the run "
        "does not start.",
        "make test-unit",
    ),
    "AC-S2": Entry(
        IMPLEMENTED,
        "A refusing licence sends the corpus to QUARANTINED with the failing rule named, "
        "through the real transition rather than an exception.",
        "make test-unit",
    ),
    "AC-S3": Entry(
        IMPLEMENTED,
        "The egress broker refuses a destination that is not on the allow list and logs "
        "the refusal; distillation is out of scope for Release 1.",
        "make test-unit",
    ),
    "AC-S4": Entry(
        IMPLEMENTED,
        "An unauthenticated request to any /v1 path is 401 and a viewer submitting is "
        "403, both as problem documents and both logged.",
        "make test-contract",
    ),
    "AC-S5": Entry(
        IMPLEMENTED,
        "Publication without a signed approval is refused; the sole approver exception "
        "is computed from the two identities and appears in the lineage.",
        "make test-contract",
    ),
    "AC-S6": Entry(
        IMPLEMENTED,
        "The emitter redacts brokered secrets, and the pre-registration scan blocks a "
        "planted test secret.",
        "make test-unit",
    ),
    "AC-S7": Entry(
        IMPLEMENTED,
        "An unsigned plug-in fails to load, and a capability the driver has not declared "
        "is refused when a job is planned rather than at review.",
        "make test-contract",
    ),
    "AC-S8": Entry(
        IMPLEMENTED,
        "Publication re-hashes the bytes on disk; a modified artefact fails against the "
        "evidence the gates recorded.",
        "make test-unit",
    ),
    "AC-S9": Entry(
        IMPLEMENTED,
        "A ledger row is rewritten in PostgreSQL with the append-only trigger disabled, "
        "and verification names the diverging sequence number.",
        "make test-degraded",
    ),
    "AC-S10": Entry(
        IMPLEMENTED,
        "The projected output is checked against free space at planning; a run that "
        "would not fit is refused before an allocation is spent.",
        "make test-unit",
    ),
    "AC-S11": Entry(
        DEVIATED,
        "The sandbox profile denies outbound network and the attempt is logged.",
        "make test-unit",
        note="The profile is generated and its content asserted; enforcing it needs the "
        "appliance's kernel. " + NO_ESTATE,
    ),
    "AC-S12": Entry(
        IMPLEMENTED,
        "gitleaks runs over the whole history on every commit and in the pipeline, and "
        "the repository is clean.",
        "make secrets",
    ),
    "AC-S13": Entry(
        IMPLEMENTED,
        "A severed link queues anchors in order and refuses release with the queue "
        "depth named; reconnecting drains in order and release unblocks.",
        "make test-degraded",
    ),
    "AC-S14": Entry(
        IMPLEMENTED,
        "The chain verifies locally with the registry unreachable, and every federation "
        "payload is built through a sealer that refuses corpus or weight content.",
        "make test-unit",
    ),
    "AC-S15": Entry(
        IMPLEMENTED,
        "An approver without a hardware factor is refused at the guard; the exception "
        "appears in the lineage output and the model card.",
        "make test-contract",
    ),
    "AC-S16": Entry(
        IMPLEMENTED,
        "The inventory is generated from the code that uses each algorithm, and every "
        "entry maps to NCSC guidance or an ISO/IEC standard.",
        "make crypto-inventory",
    ),
    "AC-S17": Entry(
        IMPLEMENTED,
        "The envelope carries a list of signatures from version one; a two algorithm "
        "envelope verifies against either.",
        "make test-unit",
    ),
    "AC-S18": Entry(
        DEVIATED,
        "The transparency log is self-hosted at MEGINGJORD and nothing reaches an "
        "external one; the egress allow list has no entry that could.",
        "make test-unit",
        note="Verified by inspection of the allow list and the release path rather than "
        "by network capture: there is no deployed release to capture. " + NO_ESTATE,
    ),
    # -- 12.2a Backend and interface ---------------------------------------
    "AC-B1": Entry(
        IMPLEMENTED,
        "Every mutating endpoint requires the key and replays the original result; the "
        "same key with a different body is refused.",
        "make test-contract",
    ),
    "AC-B2": Entry(
        IMPLEMENTED,
        "Every error is a problem document with a stable type URI, including a "
        "validation failure and an unhandled exception.",
        "make test-contract",
    ),
    "AC-B3": Entry(
        IMPLEMENTED,
        "Pagination is keyset based throughout, and a test inserting rows mid-pagination "
        "shows no skipped or duplicated record.",
        "make test-contract",
    ),
    "AC-B4": Entry(
        IMPLEMENTED,
        "A conditional write without If-Match is refused and a stale tag is 412.",
        "make test-contract",
    ),
    "AC-B5": Entry(
        IMPLEMENTED,
        "The diff gate compares the exported document against the released baseline and "
        "fails a build on a breaking change within /v1.",
        "make openapi-diff",
    ),
    "AC-B6": Entry(
        IMPLEMENTED,
        "enforce_declarations runs over the registered routes inside create_app, so a "
        "route without a declaration raises before a socket is opened.",
        "make test-contract",
    ),
    "AC-B7": Entry(
        IMPLEMENTED,
        "A violating import is planted in the domain layer and the real linter is run "
        "against the real configuration; it reports the contract broken.",
        "make test-contract",
    ),
    "AC-B8": Entry(
        IMPLEMENTED,
        "Identifiers are UUIDv7 and a property test asserts that creation order and sort "
        "order agree.",
        "make test-property",
    ),
    "AC-B9": Entry(
        IMPLEMENTED,
        "Every long operation returns 202 with a run identifier; nothing blocks a "
        "request on training.",
        "make test-contract",
    ),
    "AC-B10": Entry(
        IMPLEMENTED,
        "A query with the session variable unset returns zero rows rather than all rows, "
        "against real PostgreSQL as an unprivileged role.",
        "make test-integration",
    ),
    # -- 12.2b Frontend and experience -------------------------------------
    "AC-U1": Entry(
        IMPLEMENTED,
        "All four journeys run in Playwright against a seeded stack, with the API and "
        "the console started by the run.",
        "make test-e2e",
    ),
    "AC-U2": Entry(
        IMPLEMENTED,
        "Every exported component has a story per state, produced through the shared "
        "factory, and a component missing one fails the build.",
        "make test-frontend",
    ),
    "AC-U3": Entry(
        IMPLEMENTED,
        "The token linter walks the workspace and fails on a literal colour, spacing or "
        "radius; its own fixtures prove it catches them.",
        "make lint-web",
    ),
    "AC-U4": Entry(
        IMPLEMENTED,
        "A submitted run appears on the board within five seconds by server sent events, "
        "with zero list reads in the following six.",
        "make test-e2e",
    ),
    "AC-U5": Entry(
        IMPLEMENTED,
        "Eleven routes walked with key events only, recording every stop's accessible "
        "name and focus indicator; findings written up with what is not covered.",
        "make test-a11y",
        extra=("docs/acceptance/keyboard-pass.md",),
    ),
    "AC-U6": Entry(
        IMPLEMENTED,
        "axe over every route and every Storybook story, zero serious or critical.",
        "make test-a11y",
    ),
    "AC-U7": Entry(
        IMPLEMENTED,
        "Contrast is measured from the stylesheet in both ramps; the border token was "
        "changed rather than the threshold when it measured 1.5:1.",
        "make test-frontend",
    ),
    "AC-U8": Entry(
        IMPLEMENTED,
        "The console is driven at 320 pixels and at 200 per cent zoom with no horizontal "
        "overflow and no function lost.",
        "make test-a11y",
    ),
    "AC-U9": Entry(
        IMPLEMENTED,
        "Every transition is behind prefers-reduced-motion, asserted from the stylesheet "
        "rather than from a class name.",
        "make test-frontend",
    ),
    "AC-U10": Entry(
        IMPLEMENTED,
        "200,000 log lines render fewer than 200 DOM rows and scroll without degrading.",
        "make test-e2e",
    ),
    "AC-U11": Entry(
        IMPLEMENTED,
        "Every view states its site, and the only unscoped read is the site registry "
        "itself, which is the list of scopes rather than an aggregate across them.",
        "make test-e2e",
    ),
    "AC-U12": Entry(
        IMPLEMENTED,
        "A partitioned site is stated in words and the release control is disabled with "
        "the reason given, rather than failing with a generic error.",
        "make test-frontend",
    ),
    "AC-U13": Entry(
        IMPLEMENTED,
        "The evidence and the sole approver notice are above the decision controls, and "
        "the controls stay unavailable until the evidence has been in the viewport.",
        "make test-e2e",
    ),
    "AC-U14": Entry(
        IMPLEMENTED,
        "Every error surface carries the problem title, the available action and a "
        "copyable correlation identifier.",
        "make test-a11y",
    ),
    "AC-U15": Entry(
        IMPLEMENTED,
        "Destructive actions take two steps and state the consequence in words rather "
        "than asking whether the operator is sure.",
        "make test-e2e",
    ),
    "AC-U16": Entry(
        DEVIATED,
        "The console bundle is 66.66 kB gzipped against a 300 kB budget, checked in the build.",
        "make build-web",
        note="First contentful paint and interaction to next paint are field percentiles. "
        "There is no deployment and no field, so the bundle budget is measured and the "
        "two timing percentiles are not.",
    ),
    "AC-U17": Entry(
        IMPLEMENTED,
        "The command palette covers navigation, submission and search, and the console "
        "is walked end to end with key events only.",
        "make test-a11y",
    ),
    # -- 12.2c Quality and delivery ----------------------------------------
    "AC-Q1": Entry(
        IMPLEMENTED,
        "Every stage of SAD 11H is a job step on the main branch and none is skippable; "
        "the workflow has no continue-on-error.",
        "make ci",
    ),
    "AC-Q2": Entry(
        IMPLEMENTED,
        "Both clients are regenerated and compared; a drifted client fails the build.",
        "make clients-check",
    ),
    "AC-Q3": Entry(
        IMPLEMENTED,
        "gitleaks runs on every commit through a hook and in the pipeline over the whole "
        "history; the history is clean.",
        "make secrets",
    ),
    "AC-Q4": Entry(
        IMPLEMENTED,
        "Ledger chain invariants, specification hash determinism and projector "
        "idempotence, at 500 examples each.",
        "make test-property",
    ),
    "AC-Q5": Entry(
        IMPLEMENTED,
        "A snapshot per component per state, 175 in all, with a diff gate.",
        "make test-visual",
    ),
    "AC-Q6": Entry(
        IMPLEMENTED,
        "Every downgrade refuses; the deployment runs migrations dry first and a failed "
        "smoke test triggers rollback.",
        "make test-skills",
    ),
    "AC-Q7": Entry(
        DEVIATED,
        "The image builds from a distroless base for linux/arm64 and declares a non-root user.",
        "make images",
        note="Built and inspected on this host; running it rootless on aarch64 hardware "
        "is a commissioning check. " + NO_ESTATE,
    ),
    "AC-Q8": Entry(
        IMPLEMENTED,
        "Each of the six skills produces an artefact and the real gate is run over it, "
        "with no edit in between.",
        "make test-skills",
    ),
    "AC-Q9": Entry(
        IMPLEMENTED,
        "`make dev` brings up the stack, migrates and seeds it from a clean machine.",
        "make dev",
    ),
    # -- 12.3 Non functional ------------------------------------------------
    "AC-N1": Entry(
        NOT_BUILT,
        "Not measured.",
        "",
        note="The target is control plane CPU and memory on ALVISS during a three "
        "appliance training run. " + NO_ESTATE + " Nothing here measures it, and a "
        "figure from a developer machine would not be the figure the criterion asks "
        "for. It is a commissioning measurement.",
        uncited=True,
    ),
    "AC-N2": Entry(
        NOT_BUILT,
        "Not measured.",
        "",
        note="The target is step time within one per cent of the same job run by hand. "
        + NO_ESTATE
        + " The control plane renders a plan and does not sit in the training loop, "
        "which is the design reason to expect it; expecting is not measuring.",
    ),
    "AC-N3": Entry(
        IMPLEMENTED,
        "A state change reaches the board in under five seconds, measured in the "
        "journey against a real event stream.",
        "make test-e2e",
    ),
    "AC-N4": Entry(
        IMPLEMENTED,
        "A run list of 500 entries answers under 300 ms at the 95th percentile.",
        "make test-contract",
    ),
    "AC-N5": Entry(
        IMPLEMENTED,
        "100,000 entries verify in under 60 seconds against real PostgreSQL.",
        "make test-integration",
    ),
    "AC-N6": Entry(
        IMPLEMENTED,
        "A real API process is killed with SIGKILL and the restart reaches service in "
        "under 30 seconds, with state read back from the ledger.",
        "make test-degraded",
    ),
    "AC-N7": Entry(
        DEVIATED,
        "The core has no conditional code path on platform; every driver renders the "
        "same plan on either architecture.",
        "make test-contract",
        note="Exercised on win32 and on the linux/arm64 image build. It has not run on "
        "Apple silicon macOS, which is one of the two deployment targets. " + NO_ESTATE,
    ),
    "AC-N8": Entry(
        IMPLEMENTED,
        "90 per cent statement coverage on the core, and every transition in SAD 6.1 "
        "exercised by name.",
        "make test-unit",
    ),
    "AC-N9": Entry(
        IMPLEMENTED,
        "A new ExportDriver is added by entry point alone, under 200 lines, with no file "
        "under draupnir/ changed; an import contract holds it.",
        "make imports",
    ),
    "AC-N10": Entry(
        IMPLEMENTED,
        "The document describes every route and both clients are generated from it; "
        "neither has a method per operation, so there is nowhere to hand write one.",
        "make clients-check",
    ),
    "AC-N11": Entry(
        NOT_BUILT,
        "Not measured.",
        "",
        note="The target is an anchor round trip under two seconds at the 95th percentile "
        "over WireGuard. There is no WireGuard link and no MEGINGJORD deployment. " + NO_ESTATE,
    ),
    "AC-N12": Entry(
        DEVIATED,
        "A forge with the link down queues anchors, keeps its policy, keeps training and "
        "refuses release; nothing degrades but release.",
        "make test-degraded",
        note="The behaviour is demonstrated; 72 hours of continuous training is an "
        "endurance measurement on the estate. " + NO_ESTATE,
    ),
    # -- 12.4 Documentation -------------------------------------------------
    "AC-D1": Entry(
        IMPLEMENTED,
        "Every module README is generated from its package docstring, which carries the "
        "Owns and Must not statements of SAD 5.2; a stale README fails the build.",
        "make module-readmes",
    ),
    "AC-D2": Entry(
        IMPLEMENTED,
        "All seven extension points now have an installed reference driver; the last two "
        "-- store and policy -- were written for this criterion.",
        "make test-contract",
    ),
    "AC-D3": Entry(
        IMPLEMENTED,
        "The runbook has a section per row of SAD 11.2, and every section names the test "
        "that injects that fault for real.",
        "make test-degraded",
        extra=("docs/runbook.md",),
    ),
    "AC-D4": Entry(
        IMPLEMENTED,
        "Every SPECIFIED item of the SAD is reconciled against the repository and marked "
        "IMPLEMENTED, DEVIATED with reasons, or NOT BUILT.",
        "",
        extra=("docs/acceptance/imhotep-reconciliation.md",),
    ),
}

CRITERION_PAGE = """# {ref}

> {text}

**Priority:** {priority}
**Status:** {status}

## How it is demonstrated

{method}
{note}
{command}
## Evidence

{evidence}

---

Generated by `scripts/acceptance.py` from SAD 12 and from the citations in the
repository. Edit the register in that script, not this file.
"""


def criteria(sad: Path = SAD) -> list[Criterion]:
    """Every criterion in SAD 12, parsed from the document."""
    text = sad.read_text(encoding="utf-8")
    try:
        block = text[text.index("## 12  Acceptance criteria") : text.index("## 13  Build prompts")]
    except ValueError as error:
        msg = f"{sad} has no section 12; the pack has nothing to be about"
        raise AcceptanceError(msg) from error

    found: list[Criterion] = []
    for match in ROW.finditer(block):
        cells = [cell.strip() for cell in match.group("rest").split("|")]
        # The tables differ: functional is (criterion, priority), security is
        # (criterion, threat, priority), non functional is (criterion, target).
        # The criterion is always first and the priority, where there is one, is
        # always last.
        priority = cells[-1] if cells[-1] in {"Must", "Should"} else "Target"
        found.append(Criterion(ref=match.group(1), text=cells[0], priority=priority))
    if not found:
        msg = "SAD 12 parsed to no criteria; the table format has changed"
        raise AcceptanceError(msg)
    return found


def citations(root: Path = ROOT) -> dict[str, list[str]]:
    """Every file citing each criterion, by reference.

    Discovered rather than listed. A criterion is cited in the code that
    satisfies it, so evidence follows the code when it moves and disappears
    when the code does.
    """
    found: dict[str, set[str]] = collections.defaultdict(set)
    for name in ROOT_FILES:
        path = root / name
        if not path.is_file():
            continue
        for reference in REFERENCE.findall(path.read_text(encoding="utf-8", errors="ignore")):
            found[reference].add(name)

    for top in SEARCHED:
        base = root / top
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.suffix not in SUFFIXES or set(path.parts) & SKIP or path.name == SELF:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for reference in REFERENCE.findall(text):
                found[reference].add(path.relative_to(root).as_posix())
    return {reference: sorted(paths) for reference, paths in found.items()}


def _render(criterion: Criterion, entry: Entry, cited: list[str]) -> str:
    """One criterion's page."""
    note = f"\n{entry.note}\n" if entry.note else ""
    command = f"\n```bash\n{entry.command}\n```\n" if entry.command else ""

    lines = [f"- `{path}`" for path in [*entry.extra, *cited]]
    evidence = "\n".join(lines) if lines else "_No citation. See the note above._"

    return CRITERION_PAGE.format(
        ref=criterion.ref,
        text=criterion.text,
        priority=criterion.priority,
        status=entry.status,
        method=entry.method,
        note=note,
        command=command,
        evidence=evidence,
    )


def _index(rows: list[tuple[Criterion, Entry]]) -> str:
    """The pack's front page: every criterion, its status, and where to look."""
    counts = collections.Counter(entry.status for _, entry in rows)
    musts = [(item, entry) for item, entry in rows if item.priority == "Must"]
    must_open = [item.ref for item, entry in musts if entry.status == NOT_BUILT]

    header = f"""# Acceptance evidence

Every criterion in SAD 12, one file each, generated from the specification and
from the citations in the repository. The status is the vocabulary AC-D4 asks
for: **IMPLEMENTED**, **DEVIATED** with reasons, or **NOT BUILT**.

{len(rows)} criteria: {counts[IMPLEMENTED]} implemented, {counts[DEVIATED]} deviated,
{counts[NOT_BUILT]} not built. Of the {len(musts)} marked **Must**,
{sum(1 for _, entry in musts if entry.status != NOT_BUILT)} have evidence and
{len(must_open)} do not{"" if not must_open else " (" + ", ".join(must_open) + ")"}.

Every deviation has one cause, stated on the criterion's own page: the Sindri
estate does not exist yet, so a criterion that asks for a measurement *on that
hardware* is a commissioning measurement rather than one this build can take.
Nothing is marked implemented on the strength of an argument that it ought to
work.

Three documents sit beside the pack rather than inside it, because each is read
end to end rather than looked up:

- [`../runbook.md`](../runbook.md) — the operator runbook, one section per
  degraded mode (AC-D3).
- [`keyboard-pass.md`](keyboard-pass.md) — the manual keyboard pass and its
  findings (AC-U5).
- [`imhotep-reconciliation.md`](imhotep-reconciliation.md) — the SAD reconciled
  against the delivered repository (AC-D4).

```bash
make acceptance     # regenerate this pack
```

| Ref | Criterion | Priority | Status |
|---|---|---|---|
"""
    body = "\n".join(
        f"| [{item.ref}]({item.ref}.md) | {item.text[:96]}{'…' if len(item.text) > 96 else ''} "
        f"| {item.priority} | {entry.status} |"
        for item, entry in rows
    )
    return header + body + "\n"


def write(pack: Path = PACK) -> list[Path]:
    """Write the whole pack."""
    found = criteria()
    cited = citations()
    pack.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    rows: list[tuple[Criterion, Entry]] = []
    for criterion in found:
        entry = REGISTER.get(criterion.ref)
        if entry is None:
            msg = (
                f"{criterion.ref} is in SAD 12 and not in the register. Every criterion "
                "needs a status, and a pack with a hole in it is a pack that has not "
                "been read."
            )
            raise AcceptanceError(msg)
        rows.append((criterion, entry))
        target = pack / f"{criterion.ref}.md"
        target.write_text(
            _render(criterion, entry, cited.get(criterion.ref, [])), encoding="utf-8", newline="\n"
        )
        written.append(target)

    index = pack / "README.md"
    index.write_text(_index(rows), encoding="utf-8", newline="\n")
    written.append(index)
    return written


def problems() -> list[str]:
    """Every way the pack does not hold up. Empty means it does."""
    found = criteria()
    cited = citations()
    refs = {criterion.ref for criterion in found}
    issues: list[str] = []

    for extra in sorted(set(REGISTER) - refs):
        issues.append(f"{extra} is in the register and not in SAD 12")

    for criterion in found:
        entry = REGISTER.get(criterion.ref)
        if entry is None:
            issues.append(f"{criterion.ref} has no register entry")
            continue
        if entry.status not in {IMPLEMENTED, DEVIATED, NOT_BUILT}:
            issues.append(f"{criterion.ref} has status {entry.status!r}, which is not AC-D4's")
        if entry.status in {DEVIATED, NOT_BUILT} and not entry.note:
            issues.append(f"{criterion.ref} is {entry.status} and says no reason")
        if entry.status == IMPLEMENTED and not (cited.get(criterion.ref) or entry.extra):
            issues.append(
                f"{criterion.ref} claims IMPLEMENTED and nothing in the repository cites "
                "it. Cite it where it is satisfied, or say why it deviates."
            )
        if entry.uncited and cited.get(criterion.ref):
            issues.append(f"{criterion.ref} is marked uncited and is cited")
        for path in entry.extra:
            if not (ROOT / path).exists():
                issues.append(f"{criterion.ref} names {path}, which does not exist")

        target = PACK / f"{criterion.ref}.md"
        expected = _render(criterion, entry, cited.get(criterion.ref, []))
        if not target.is_file() or target.read_text(encoding="utf-8") != expected:
            issues.append(f"{criterion.ref}.md is stale. Run `make acceptance`.")

    rows = [
        (criterion, REGISTER[criterion.ref]) for criterion in found if criterion.ref in REGISTER
    ]
    index = PACK / "README.md"
    if not index.is_file() or index.read_text(encoding="utf-8") != _index(rows):
        issues.append("docs/acceptance/README.md is stale. Run `make acceptance`.")

    return issues


def main(argv: list[str] | None = None) -> int:
    """Write the pack, or check it."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if stale or unbacked")
    args = parser.parse_args(argv)

    if args.check:
        issues = problems()
        for issue in issues:
            print(f"  {issue}")
        if issues:
            return 1
        print(f"{len(criteria())} criteria, every one with a status and evidence")
        return 0

    written = write()
    print(f"wrote {len(written)} files to {PACK.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
