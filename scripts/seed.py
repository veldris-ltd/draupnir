"""Seed a realistic development dataset.

Produces, exactly as Prompt 0 specifies:

    2 sites, 6 sources, 14 runs across every state, 1 release,
    400 ledger entries

The dataset is deterministic. Identifiers are UUIDv7 built from a fixed epoch
and a seeded stream, so two developers running `make dev` get byte-identical
databases and a screenshot in a bug report means something.

Since Prompt 1 the ledger is the source of truth and `run` is a projection of
it. The seed therefore writes chains, not rows: it appends one registration
entry and one entry per transition the run has actually made, each carrying
the fields SAD 6.1 requires of it, and then asks the projector to build the
registry. That is the same code path a live transition takes, so a dataset
that seeds is a dataset the projector can rebuild.

Two modelling notes, because the seed is where they first bite.

  * SAD 6.1 tabulates fourteen states. Every run traverses the machine from
    DRAFT, so every run passes through CORPUS_REGISTERED and LICENCE_CLEARED;
    but no run *rests* there, because those two describe a corpus awaiting a
    judgement rather than work in progress. `source.state` carries them, and
    the twelve runs below rest in the twelve remaining states, one each.
  * SAD 11C constraint 3 puts row level security on the site scoped tables, so
    the seed sets `draupnir.site_id` per site and writes each site's rows in
    its own transaction. If that variable were not set, every insert below
    would be refused, which is the point.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Any
from uuid import UUID

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection

from draupnir.brisingamen import sweep as sweeps
from draupnir.core.domain import gate_results as gate_record
from draupnir.core.domain import releases as release_record
from draupnir.core.domain.evidence import Evidence
from draupnir.core.domain.federation import ANCHOR_SUBMITTED
from draupnir.core.domain.identifiers import id_at
from draupnir.core.domain.ledger import GENESIS_HASH, LedgerEntry, compute_entry_hash
from draupnir.core.domain.projector import REGISTRATION
from draupnir.core.domain.sites import SiteScope
from draupnir.core.domain.states import RUN_PHASE_STATES, RunState, Transition, find
from draupnir.core.infrastructure.config import get_settings
from draupnir.core.infrastructure.repositories import (
    GateResultProjection,
    ReleaseProjection,
    RunProjection,
)
from draupnir.gleipnir import licence as licence_policy
from draupnir.hamarr import tiers
from draupnir.hodd import retention as retention_record
from draupnir.interfaces.types import GateOutcome
from draupnir.motsognir import arrays
from draupnir.worker.accepted import ANSWERS

SEED = 20260901
EPOCH = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)

TARGET_LEDGER_ENTRIES = 400
TARGET_RUNS = 14
TARGET_SOURCES = 6
#: One, because one run is released. RF-33: artefacts, approvals and releases
#: are projected from the chain, so a release is a publication of a released
#: run's own approved artefact. The three this used to insert paired artefacts
#: with approvals by list position, including approvals of runs still awaiting
#: one -- rows no chain could produce.
TARGET_RELEASES = 1


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SITES: tuple[dict[str, Any], ...] = (
    {
        "id": "sindri",
        "name": "Sindri",
        "location": "Nuneaton, United Kingdom",
        "timezone": "Europe/London",
        "control_plane_uri": "https://alviss.sindri.veldris.internal",
        "anchor_state": "ANCHORED",
        "anchored_offset_hours": 2,
    },
    {
        "id": "brokkr",
        "name": "Brokkr",
        "location": "Nuneaton, United Kingdom",
        "timezone": "Europe/London",
        "control_plane_uri": "https://alviss.brokkr.veldris.internal",
        "anchor_state": "UNANCHORED",
        "anchored_offset_hours": None,
    },
)

SOURCES: tuple[dict[str, Any], ...] = (
    {
        "jurisdiction": "GBR",
        "url": "https://www.legislation.gov.uk/ukpga",
        "licence_spdx": "OGL-UK-3.0",
        "attribution_required": True,
        "personal_data": False,
        "dpia_ref": None,
        "residency_constraint": ["sindri", "brokkr"],
        "state": RunState.CURATED,
    },
    {
        "jurisdiction": "GBR",
        "url": "https://caselaw.nationalarchives.gov.uk",
        "licence_spdx": "OGL-UK-3.0",
        "attribution_required": True,
        "personal_data": True,
        "dpia_ref": "DPIA-2026-014",
        "residency_constraint": ["sindri"],
        "state": RunState.CURATED,
    },
    {
        "jurisdiction": "IRL",
        "url": "https://www.irishstatutebook.ie",
        "licence_spdx": "CC-BY-4.0",
        "attribution_required": True,
        "personal_data": False,
        "dpia_ref": None,
        "residency_constraint": [],
        "state": RunState.LICENCE_CLEARED,
    },
    {
        "jurisdiction": "DEU",
        "url": "https://www.gesetze-im-internet.de",
        "licence_spdx": "CC0-1.0",
        "attribution_required": False,
        "personal_data": False,
        "dpia_ref": None,
        "residency_constraint": [],
        "state": RunState.CORPUS_REGISTERED,
    },
    {
        "jurisdiction": "USA",
        "url": "https://www.govinfo.gov/bulkdata/USCODE",
        "licence_spdx": "CC-BY-SA-4.0",
        "attribution_required": True,
        "personal_data": False,
        "dpia_ref": None,
        "residency_constraint": [],
        "state": RunState.DRAFT,
    },
    {
        # Refused by GLEIPNIR licence policy, retained with its history.
        "jurisdiction": "FRA",
        "url": "https://example-aggregator.invalid/fr-corpus",
        "licence_spdx": "LicenseRef-Proprietary-Unclear",
        "attribution_required": True,
        "personal_data": True,
        "dpia_ref": "DPIA-2026-021",
        "residency_constraint": ["sindri"],
        "state": RunState.QUARANTINED,
    },
)

#: One run per run-phase state, in lifecycle order, alternating across sites.
RUN_PLAN = (
    ("cim-usa-v0.1", "adapter", RunState.DRAFT, "sindri"),
    ("cim-deu-v0.1", "adapter", RunState.CURATED, "brokkr"),
    ("cim-irl-v0.2", "adapter", RunState.QUEUED, "sindri"),
    ("cim-gbr-v0.4", "adapter", RunState.TRAINING, "sindri"),
    ("cim-gbr-v0.3", "adapter", RunState.TRAINED, "sindri"),
    ("cim-esp-v0.1", "adapter", RunState.FAILED, "brokkr"),
    ("cim-nld-v0.1", "adapter", RunState.EVALUATING, "brokkr"),
    ("cim-gbr-v0.2", "merge", RunState.MERGED, "sindri"),
    ("cim-irl-v0.1", "merge", RunState.QUANTISED, "sindri"),
    # AWAITING_APPROVAL sits at the same site as RELEASED on purpose. The
    # approval journey and the audit journey are one chain -- approve, then
    # walk the lineage of what was approved -- and splitting the two stages
    # across sites makes that chain unwalkable at either of them, because
    # reads are site scoped and no view aggregates across sites (AC-U11).
    ("cim-aus-v0.1", "adapter", RunState.AWAITING_APPROVAL, "sindri"),
    ("cim-gbr-v0.1", "adapter", RunState.RELEASED, "sindri"),
    ("cim-fra-v0.1", "adapter", RunState.QUARANTINED, "brokkr"),
    # A second artefact awaiting approval, which journey J3 decides (RF-32). J3
    # used to open the decision dialogs and confirm neither, which is how a
    # console whose decision control could not succeed passed its journey. A
    # decision consumes its gate, so the one J3 decides is its own: cim-aus-v0.1
    # stays pending for the other approval journeys and for the keyboard walk
    # of stage 2.8. Last in the plan, so it is the newest in the queue and the
    # generated values of every run before it are unchanged.
    ("cim-nzl-v0.1", "adapter", RunState.AWAITING_APPROVAL, "sindri"),
    # A third, which J3 approves end to end (RF-40). An approval could not
    # succeed from the console until the signing agent signed it, so J3 only
    # opened the dialog. Approving consumes the gate as rejecting does, so it is
    # J3's own, and last in the plan for the same reason as cim-nzl-v0.1.
    ("cim-fji-v0.1", "adapter", RunState.AWAITING_APPROVAL, "sindri"),
)

GATES = ("E1", "E2", "E3", "E4", "E5", "E6")
GATE_SUITE_VERSION = "raun-suite/2026.02"

#: The spine of SAD 6.1: the path a run walks when nothing goes wrong.
SPINE: tuple[RunState, ...] = (
    RunState.DRAFT,
    RunState.CORPUS_REGISTERED,
    RunState.LICENCE_CLEARED,
    RunState.CURATED,
    RunState.QUEUED,
    RunState.TRAINING,
    RunState.TRAINED,
    RunState.EVALUATING,
    RunState.MERGED,
    RunState.QUANTISED,
    RunState.AWAITING_APPROVAL,
    RunState.RELEASED,
)

ACTOR_BY_TARGET = {
    RunState.CORPUS_REGISTERED: "curator@veldris.internal",
    RunState.LICENCE_CLEARED: "system:gleipnir",
    RunState.CURATED: "curator@veldris.internal",
    RunState.QUEUED: "system:motsognir",
    RunState.TRAINING: "system:motsognir",
    RunState.TRAINED: "system:hamarr",
    RunState.FAILED: "system:hamarr",
    RunState.EVALUATING: "system:raun",
    RunState.MERGED: "system:brisingamen",
    RunState.QUANTISED: "system:skidbladnir",
    RunState.AWAITING_APPROVAL: "system:gleipnir",
    RunState.RELEASED: "approver@veldris.internal",
    RunState.QUARANTINED: "approver@veldris.internal",
}

#: Operational entries used to bring each chain up to its share of the 400.
BACKGROUND_EVENTS = (
    ("site", "ANCHOR_SUBMITTED", "system:gullinbursti"),
    ("site", "POLICY_PULLED", "system:gullinbursti"),
    ("site", "CAPACITY_REPORTED", "system:gullinbursti"),
    ("plugin", "PLUGIN_VERIFIED", "system:svalinn"),
    ("artefact", "ARTEFACT_SEALED", "system:hodd"),
)

PLUGINS = (
    ("hamarr.llamafactory", "1.4.0", "TrainingDriver", True, True),
    ("hamarr.axolotl", "0.9.2", "TrainingDriver", True, False),
    ("motsognir.slurm", "1.0.3", "SchedulerDriver", True, True),
    ("raun.lmeval", "2.1.0", "EvaluationDriver", True, True),
    ("brisingamen.mergekit", "0.6.1", "MergeDriver", True, True),
    ("skidbladnir.llamacpp", "1.2.0", "QuantisationDriver", True, True),
)

#: The seeded adapter array: its name, scheduler job, and the appliances its
#: elements run on, one each (SAD 5.2).
#: The floor both of the seeded sweep's gates are judged against.
SWEEP_FLOOR = 0.74

ARRAY_NAME = "cim-56-adapters"
ARRAY_JOB = "4821"
ARRAY_APPLIANCES = ("dvalin", "durin", "dain")
ARRAY_RETRY_BUDGET = 3

#: The elements of the seeded array that have moved since submission: index,
#: state, attempts, exit code. RF-27: S12's primary action requeues an element
#: that stopped without completing, and a seeded stack whose array had none of
#: those would give the journey that performs it nothing to press. One in each
#: such state, beside elements that finished, elements still running, and the
#: pending remainder the monitor exists to show.
ARRAY_OBSERVED: tuple[tuple[int, str, int, int | None], ...] = (
    *((index, "COMPLETED", 1, 0) for index in range(10)),
    (10, "RUNNING", 1, None),
    (11, "RUNNING", 1, None),
    (12, "RUNNING", 1, None),
    (13, "AWAITING_RETRY", 1, 137),
    (14, "EXHAUSTED", 4, 137),
    (15, "FAILED", 1, 1),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class Chain:
    """Running state of one site's ledger chain."""

    site_id: str
    seq: int = 0
    prev_hash: str = GENESIS_HASH
    rows: list[dict[str, Any]] | None = None

    def append(
        self,
        *,
        ts: datetime,
        actor: str,
        subject_type: str,
        subject_id: str,
        transition: str,
        payload: dict[str, Any],
        entry_id: UUID,
    ) -> None:
        """Extend the chain by one entry, hashed exactly as SAD 7.1 requires."""
        if self.rows is None:
            self.rows = []
        entry_hash = compute_entry_hash(self.prev_hash, payload)
        self.rows.append(
            {
                "id": entry_id,
                "site_id": self.site_id,
                "seq": self.seq + 1,
                "prev_hash": self.prev_hash,
                "entry_hash": entry_hash,
                "ts": ts,
                "actor": actor,
                "subject_type": subject_type,
                "subject_id": subject_id,
                "transition": transition,
                "payload": json.dumps(payload, sort_keys=True),
            }
        )
        self.seq += 1
        self.prev_hash = entry_hash


class Ids:
    """Deterministic UUIDv7 factory."""

    def __init__(self, rng: random.Random) -> None:
        """Take the seeded stream the whole dataset shares."""
        self._rng = rng

    def next(self, moment: datetime) -> UUID:
        """Return a fresh identifier stamped at `moment`."""
        return id_at(moment, self._rng.randbytes(10))


def fake_sha256(rng: random.Random, label: str) -> str:
    """A stable, obviously synthetic digest. Real hashes come from HODD."""
    return hashlib.sha256(f"{label}:{rng.random()}".encode()).hexdigest()


def _minutes(rng: random.Random, low: int, high: int) -> timedelta:
    return timedelta(minutes=rng.randint(low, high))


def lifecycle(state: RunState) -> tuple[RunState, ...]:
    """Return the states a run resting in `state` has actually walked.

    Every path starts at DRAFT and follows the spine, so the two corpus states
    are traversed by every run even though none rests in them.
    """
    if state is RunState.FAILED:
        return (*SPINE[: SPINE.index(RunState.TRAINING) + 1], RunState.FAILED)
    if state is RunState.QUARANTINED:
        return (*SPINE[: SPINE.index(RunState.AWAITING_APPROVAL) + 1], RunState.QUARANTINED)
    return SPINE[: SPINE.index(state) + 1]


def transition_payload(
    transition: Transition, *, name: str, rng: random.Random, decided_at: datetime
) -> dict[str, Any]:
    """Build a payload carrying every field SAD 6.1 requires of this transition.

    Keyed by the transition rather than by the target state, because the table
    is what states the requirement, and `states.missing_records` checks against
    exactly this.
    """
    built: dict[str, dict[str, Any]] = {
        "DRAFT->CORPUS_REGISTERED": {
            "sources": [f"src-{index}" for index in range(rng.randint(2, 5))],
            "source_sha256": fake_sha256(rng, f"sources:{name}"),
            "curator": "curator@veldris.internal",
        },
        "CORPUS_REGISTERED->LICENCE_CLEARED": {
            # A version the policy registry holds (RF-34). This named one it did
            # not, so the seeded release could not have rendered its copyright
            # policy under the version it was cleared under.
            "policy_version": licence_policy.CURRENT.version,
            "evaluation_result": "PASS",
        },
        "LICENCE_CLEARED->CURATED": {
            "stage_retention": {"dedupe": 0.82, "quality": 0.61, "decontaminate": 0.99},
            "output_sha256": fake_sha256(rng, f"curated:{name}"),
            "token_count": rng.randint(120_000_000, 900_000_000),
        },
        "CURATED->QUEUED": {
            "spec_hash": fake_sha256(rng, f"spec:{name}"),
            "input_artefact_sha256": [fake_sha256(rng, f"input:{name}")],
        },
        "QUEUED->TRAINING": {
            "scheduler_job_id": str(rng.randint(100000, 999999)),
            "node": rng.choice(("dvalin", "durin", "dain")),
            "placement": {"partition": "adapters", "nodes": 1},
        },
        "TRAINING->TRAINED": {
            "checkpoint_sha256": fake_sha256(rng, f"ckpt:{name}"),
            "steps": rng.randint(1200, 4800),
            "final_loss": round(rng.uniform(0.7, 1.4), 4),
        },
        "TRAINING->FAILED": {
            "exit_code": rng.choice((1, 137)),
            "last_log_lines": ["CUDA out of memory on rank 0"],
            "resource_state": {"gpu_memory_used_gb": 139.6},
        },
        "TRAINED->EVALUATING": {
            "suite_version": GATE_SUITE_VERSION,
            "baseline": "run://MIDGARD-CORE-QWEN36-35B-A3B-v1.0",
        },
        "EVALUATING->MERGED": {
            "gate_results": {gate: {"passed": True} for gate in GATES},
        },
        "EVALUATING->QUEUED": {
            "failing_gate": "E3",
            "requeue_reason": "gate E3 below baseline, retry budget remaining",
        },
        "MERGED->QUANTISED": {
            "merge_config_hash": fake_sha256(rng, f"merge:{name}"),
            "sweep_result": {"points": 5, "selected": 0.6},
        },
        "QUANTISED->AWAITING_APPROVAL": {
            "format_gate_results": {
                fmt: {"passed": True} for fmt in ("nvfp4", "gguf-q4km", "mlx4")
            },
        },
        "AWAITING_APPROVAL->RELEASED": {
            "approver": "approver@veldris.internal",
            "signature": fake_sha256(rng, f"sig:{name}"),
            "decided_at": decided_at.isoformat(),
        },
        "AWAITING_APPROVAL->QUARANTINED": {
            "rejection_reason": "Licence policy refusal on a constituent source",
        },
    }
    return built[transition.name]


def gate_evidence(
    name: str, kind: str, decided_at: datetime, *, digest: str | None = None
) -> dict[str, Any]:
    """A passing evaluation, in the shape `Evidence.as_payload()` records. RF-41.

    Every gate carries its value, baseline and margin, so `gate_result` is
    projected from the chain as it is on an estate. Drawn from a stream of its
    own per run and kind, so the values repeat on every seed without drawing on
    the stream the rest of the dataset is generated from.
    """
    stream = random.Random(f"gates:{kind}:{name}")  # noqa: S311 -- a fixture, not a security decision
    gates: dict[str, dict[str, Any]] = {}
    for gate in GATES:
        baseline = round(stream.uniform(0.58, 0.74), 4)
        value = round(baseline + stream.uniform(0.001, 0.06), 4)
        gates[gate] = {
            "value": value,
            "baseline": baseline,
            "margin": round(value - baseline, 4),
            "passed": True,
        }
    return {
        "artefactSha256": digest or fake_sha256(stream, f"evaluated:{kind}:{name}"),
        "artefactKind": kind,
        "suite": "general-core" if kind == "adapter" else "release",
        "suiteVersion": GATE_SUITE_VERSION,
        "baselineSha256": None,
        "evaluatedAt": (decided_at - timedelta(hours=12)).isoformat(),
        "passed": True,
        "failing": [],
        "gates": gates,
    }


def stored_facts(
    transition: Transition,
    *,
    site_id: str,
    name: str,
    rng: random.Random,
    decided_at: datetime,
    quantised: str | None,
) -> dict[str, Any]:
    """What the worker and the API record about stored artefacts and decisions. RF-33.

    `transition_payload` carries what SAD 6.1 requires of a transition. This
    carries what the release projection folds, in the shapes the worker and
    `decideGate` record them:
    - the artefacts a run stored, with address, digest, kind and size;
    - the bytes it awaits approval on;
    - who decided, and on what.

    So the artefact, approval and release rows the seed used to insert beside
    the chain are projected from it, like everything else.
    """

    def stored(kind: str, path: str) -> dict[str, Any]:
        return {
            "uri": f"hodd://{site_id}/{path}",
            "sha256": fake_sha256(rng, path),
            "kind": kind,
            "size": rng.randint(2 * 10**8, 9 * 10**9),
        }

    if transition.name == "TRAINING->TRAINED":
        adapter = stored("adapter", f"adapters/{name}")
        return {
            "artefact_sha256": adapter["sha256"],
            "artefact_uri": adapter["uri"],
            "artefacts": [adapter],
        }
    if transition.name == "EVALUATING->MERGED":
        # RF-41. What the worker records when the gates pass: the evidence in
        # full, so `gate_result` is projected from it rather than inserted.
        return {"gate_results": gate_evidence(name, "adapter", decided_at)}
    if transition.name == "MERGED->QUANTISED":
        nvfp4 = stored("quantised", f"models/{name}/nvfp4")
        return {"formats_built": {"nvfp4": nvfp4["sha256"]}, "artefacts": [nvfp4]}
    if transition.name == "QUANTISED->AWAITING_APPROVAL":
        return {
            "artefact_sha256": quantised,
            "formats": ["nvfp4"],
            "model": name,
            # The re-gate of every built format, as the worker records it.
            "format_gate_results": {
                "nvfp4": {
                    **gate_evidence(name, "quantised", decided_at, digest=quantised),
                    "format": "nvfp4",
                }
            },
        }
    if transition.name == "AWAITING_APPROVAL->RELEASED":
        return {
            "artefact_sha256": quantised,
            "model": name,
            "sole_approver_exception": False,
            "signature_verified": True,
        }
    if transition.name == "AWAITING_APPROVAL->QUARANTINED":
        return {
            "artefact_sha256": quantised,
            "approver": "approver@veldris.internal",
            "decided_at": decided_at.isoformat(),
        }
    return {}


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------


def build() -> dict[str, Any]:
    """Build the whole dataset in memory, then hand it to the writer."""
    rng = random.Random(SEED)  # noqa: S311 -- a fixture, not a security decision
    ids = Ids(rng)

    sites: list[dict[str, Any]] = []
    for site in SITES:
        offset = site["anchored_offset_hours"]
        sites.append(
            {
                "id": site["id"],
                "name": site["name"],
                "location": site["location"],
                "timezone": site["timezone"],
                "control_plane_uri": site["control_plane_uri"],
                "anchor_state": site["anchor_state"],
                "last_anchored_at": None
                if offset is None
                else EPOCH + timedelta(days=40) - timedelta(hours=int(offset)),
            }
        )

    chains = {str(site["id"]): Chain(site_id=str(site["id"])) for site in SITES}
    clock = {str(site["id"]): EPOCH for site in SITES}

    def tick(site_id: str, low: int = 15, high: int = 240) -> datetime:
        clock[site_id] = clock[site_id] + _minutes(rng, low, high)
        return clock[site_id]

    # -- sources ------------------------------------------------------------
    sources: list[dict[str, Any]] = []
    for index, spec in enumerate(SOURCES):
        moment = EPOCH + timedelta(hours=index)
        source_id = ids.next(moment)
        digest = fake_sha256(rng, str(spec["url"]))
        sources.append(
            {
                "id": source_id,
                "jurisdiction": spec["jurisdiction"],
                "url": spec["url"],
                "licence_spdx": spec["licence_spdx"],
                "attribution_required": spec["attribution_required"],
                "retrieved_at": moment,
                "sha256": digest,
                "personal_data": spec["personal_data"],
                "dpia_ref": spec["dpia_ref"],
                "residency_constraint": spec["residency_constraint"],
                "state": str(spec["state"]),
            }
        )
        site_id = "sindri" if index % 2 == 0 else "brokkr"
        chains[site_id].append(
            ts=tick(site_id, 5, 45),
            actor="curator@veldris.internal",
            subject_type="source",
            subject_id=str(source_id),
            transition=f"DRAFT->{spec['state']}",
            payload={
                "url": spec["url"],
                "licence_spdx": spec["licence_spdx"],
                "sha256": digest,
                "personal_data": spec["personal_data"],
                "jurisdiction": spec["jurisdiction"],
            },
            entry_id=ids.next(clock[site_id]),
        )

    # -- runs: a registration entry, then one entry per transition ----------
    runs: list[dict[str, Any]] = []
    #: Each run's quantised artefact, and where its approval sits in the chain:
    #: what a publication names.
    quantised_of: dict[UUID, str] = {}
    approved_at: dict[UUID, int] = {}

    for index, (name, kind, state, site_id) in enumerate(RUN_PLAN):
        started = EPOCH + timedelta(days=2 + index, hours=rng.randint(0, 8))
        run_id = ids.next(started)
        spec_hash = fake_sha256(rng, f"spec:{name}")
        decided = started + timedelta(days=1)

        chains[site_id].append(
            ts=tick(site_id, 10, 90),
            actor="curator@veldris.internal",
            subject_type="run",
            subject_id=str(run_id),
            transition=REGISTRATION,
            payload={"name": name, "spec_hash": spec_hash, "kind": kind},
            entry_id=ids.next(clock[site_id]),
        )

        for source_state, target_state in pairwise(lifecycle(state)):
            transition = find(source_state, target_state)
            if transition is None:  # pragma: no cover -- the spine is in the table
                msg = f"{source_state}->{target_state} is not a transition in SAD 6.1"
                raise RuntimeError(msg)
            required = transition_payload(transition, name=name, rng=rng, decided_at=decided)
            facts = stored_facts(
                transition,
                site_id=site_id,
                name=name,
                rng=rng,
                decided_at=decided,
                quantised=quantised_of.get(run_id),
            )
            chains[site_id].append(
                ts=tick(site_id, 20, 300),
                actor=ACTOR_BY_TARGET[target_state],
                subject_type="run",
                subject_id=str(run_id),
                transition=transition.name,
                payload={**required, **facts},
                entry_id=ids.next(clock[site_id]),
            )
            if transition.name == "MERGED->QUANTISED":
                quantised_of[run_id] = str(facts["formats_built"]["nvfp4"])
            if transition.name == "AWAITING_APPROVAL->RELEASED":
                approved_at[run_id] = chains[site_id].seq

        runs.append({"id": run_id, "site_id": site_id, "name": name, "state": str(state)})
        # No gate rows here (RF-41). The evaluations are recorded on the chain
        # above, in the worker's shape, and `gate_result` is projected from them.

    # -- releases: each released run's approved artefact, published ---------
    # As `publishRelease` records one (RF-33): against the artefact, naming the
    # approval it rests on, and then countersigned by an anchor covering it.
    # The release, its approval and its artefact are projected from these
    # entries rather than inserted beside them.
    for run in runs:
        if run["state"] != str(RunState.RELEASED):
            continue
        site_id = str(run["site_id"])
        digest = quantised_of[run["id"]]
        chains[site_id].append(
            ts=tick(site_id, 10, 60),
            actor="approver@veldris.internal",
            subject_type=release_record.RELEASE_SUBJECT,
            subject_id=digest,
            transition=release_record.PUBLISHED,
            payload={
                "artefact_sha256": digest,
                "run_id": str(run["id"]),
                "approved_at_seq": approved_at[run["id"]],
                "approver": "approver@veldris.internal",
                "licence_policy_version": licence_policy.CURRENT.version,
            },
            entry_id=ids.next(clock[site_id]),
        )
        anchored_at = tick(site_id, 5, 20)
        chains[site_id].append(
            ts=anchored_at,
            actor="system:gullinbursti",
            subject_type="site",
            subject_id=site_id,
            transition=ANCHOR_SUBMITTED,
            payload={
                "anchored_through": chains[site_id].seq,
                "anchored_at": anchored_at.isoformat(),
            },
            entry_id=ids.next(clock[site_id]),
        )

    # -- the adapter array, part way through --------------------------------
    # Written as the API and the worker write it: accepted, then submitted as
    # one scheduler array answering that acceptance, then observed element by
    # element. S12 folds exactly these entries (RF-13), so a seeded array that
    # took a shortcut would be an array the monitor reads differently.
    array_site = "sindri"
    array_chain = chains[array_site]
    size = len(tiers.ALL)
    throttle = f"0-{size - 1}%{len(ARRAY_APPLIANCES)}"
    array_chain.append(
        ts=tick(array_site, 10, 60),
        actor="operator@veldris.internal",
        subject_type=arrays.ARRAY_SUBJECT,
        subject_id=ARRAY_NAME,
        transition=arrays.ARRAY_ACCEPTED,
        payload={
            "name": ARRAY_NAME,
            "subjects": list(tiers.ALL),
            "retryBudget": ARRAY_RETRY_BUDGET,
            "run_id": str(ids.next(clock[array_site])),
        },
        entry_id=ids.next(clock[array_site]),
    )
    array_chain.append(
        ts=tick(array_site, 1, 5),
        actor="system:motsognir",
        subject_type=arrays.ARRAY_SUBJECT,
        subject_id=ARRAY_NAME,
        transition=arrays.ARRAY_SUBMITTED,
        payload={
            "subject": ARRAY_NAME,
            ANSWERS: array_chain.seq,
            "name": ARRAY_NAME,
            "size": size,
            "concurrency": len(ARRAY_APPLIANCES),
            "slurmArray": throttle,
            "arguments": [f"--array={throttle}", "--partition=adapters"],
            "partition": "adapters",
            "appliances": list(ARRAY_APPLIANCES),
            "retryBudget": ARRAY_RETRY_BUDGET,
            "jobId": ARRAY_JOB,
            "driver": "motsognir.slurm/v1",
            "elements": [
                {
                    "index": index,
                    "subject": subject,
                    "state": "PENDING",
                    "attempts": 0,
                    "jobId": f"{ARRAY_JOB}_{index}",
                    "node": None,
                    "exitCode": None,
                }
                for index, subject in enumerate(tiers.ALL)
            ],
        },
        entry_id=ids.next(clock[array_site]),
    )
    # `element_state`, not `state`: the run loop above binds `state` to a
    # `RunState`, and an element state is a different vocabulary (SAD 5.2).
    for index, element_state, attempts, exit_code in ARRAY_OBSERVED:
        array_chain.append(
            ts=tick(array_site, 20, 240),
            actor="system:motsognir",
            subject_type=arrays.ARRAY_SUBJECT,
            subject_id=ARRAY_NAME,
            transition=arrays.ELEMENT_OBSERVED,
            payload={
                "element": {
                    "index": index,
                    "subject": tiers.ALL[index],
                    "state": element_state,
                    "attempts": attempts,
                    "jobId": f"{ARRAY_JOB}_{index}",
                    "node": ARRAY_APPLIANCES[index % len(ARRAY_APPLIANCES)],
                    "exitCode": exit_code,
                }
            },
            entry_id=ids.next(clock[array_site]),
        )

    # -- one raw corpus past retention, proposed and not yet approved -------
    # S06's primary action approves a deletion (RF-27), and a seeded stack
    # with nothing due would give the journey that performs it nothing to
    # approve. Written as the daily duty writes a proposal: the corpus, its
    # jurisdiction, the raw corpus a deletion removes and the released run built
    # from it -- and past due on any clock the stack runs against.
    released_run = next(run for run in runs if run["name"] == "cim-gbr-v0.1")
    retention_site = str(released_run["site_id"])
    corpus = fake_sha256(rng, "corpus:GBR:curated")
    last_release = EPOCH - timedelta(days=800)
    chains[retention_site].append(
        ts=tick(retention_site, 10, 60),
        actor="system:worker",
        subject_type=retention_record.CORPUS_SUBJECT,
        subject_id=corpus,
        transition=retention_record.PROPOSED,
        payload={
            "corpusSha256": corpus,
            "curatedBy": str(released_run["id"]),
            "lastReleaseAt": last_release.isoformat(),
            "dueAt": retention_record.due_at(last_release).isoformat(),
            "releases": [str(released_run["id"])],
            "policy": "raw-corpus",
            "retentionMonths": retention_record.RETENTION_MONTHS,
            "jurisdiction": "GBR",
            "artefact": f"hodd://{retention_site}/corpora/GBR/raw",
        },
        entry_id=ids.next(clock[retention_site]),
    )

    # -- the merged run's sweep, evaluated and not yet chosen ----------------
    # S15's primary action chooses a merge point (RF-27), from the sweep the
    # worker merged and re-gated point by point. Written as the worker writes
    # it, against the run as a `sweep` subject. Five points on two gates: the
    # lightest blend misses E1's floor, the heaviest misses E2's, and the three
    # between pass both -- the trade the screen exists to present.
    merged_run = next(run for run in runs if run["name"] == "cim-gbr-v0.2")
    sweep_site = str(merged_run["site_id"])
    seeded_sweep = sweeps.linear(
        method="slerp",
        base_sha256=fake_sha256(rng, "base:MIDGARD-CORE"),
        adapter_sha256=fake_sha256(rng, "adapter:cim-gbr-v0.2"),
    )
    evaluated_at = tick(sweep_site, 10, 60)
    for index, point in enumerate(seeded_sweep.points):
        digest = fake_sha256(rng, f"merged:cim-gbr-v0.2:{index}")
        weight = float(point.parameters["weight"])
        scores = {"E1": round(0.70 + 0.12 * weight, 4), "E2": round(0.86 - 0.14 * weight, 4)}
        outcomes = tuple(
            GateOutcome(
                gate=gate,
                suite_version="general-core/2026.01",
                value=value,
                baseline_value=SWEEP_FLOOR,
                margin=round(value - SWEEP_FLOOR, 4),
                passed=value >= SWEEP_FLOOR,
            )
            for gate, value in scores.items()
        )
        seeded_sweep = seeded_sweep.with_result(
            point.parameters,
            artefact_sha256=digest,
            evidence=Evidence(
                artefact_sha256=digest,
                artefact_kind="merged",
                outcomes=outcomes,
                passed=all(outcome.passed for outcome in outcomes),
                suite="general-core",
                suite_version="general-core/2026.01",
                evaluated_at=evaluated_at,
                measurements=scores,
            ),
        )
    chains[sweep_site].append(
        ts=evaluated_at,
        actor="system:worker",
        subject_type=sweeps.SWEEP_SUBJECT,
        subject_id=str(merged_run["id"]),
        transition=sweeps.EVALUATED,
        payload=sweeps.record(seeded_sweep),
        entry_id=ids.next(clock[sweep_site]),
    )

    # -- pad the chains to exactly 400 entries ------------------------------
    written = sum(chain.seq for chain in chains.values())
    order = [str(site["id"]) for site in SITES]
    while written < TARGET_LEDGER_ENTRIES:
        site_id = order[written % len(order)]
        subject_type, transition_name, actor = BACKGROUND_EVENTS[written % len(BACKGROUND_EVENTS)]
        chain = chains[site_id]
        chain.append(
            ts=tick(site_id, 5, 90),
            actor=actor,
            subject_type=subject_type,
            subject_id=site_id if subject_type == "site" else str(ids.next(clock[site_id])),
            transition=transition_name,
            payload={"seq": chain.seq + 1, "head": chain.prev_hash[:16], "note": "operational"},
            entry_id=ids.next(clock[site_id]),
        )
        written += 1

    plugins = [
        {
            "name": name,
            "version": version,
            "interface": interface,
            "signature_verified": verified,
            "capabilities": json.dumps({"declared": True}),
            "enabled": enabled,
        }
        for name, version, interface, verified, enabled in PLUGINS
    ]

    return {
        "sites": sites,
        "sources": sources,
        "runs": runs,
        "plugins": plugins,
        "chains": chains,
    }


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def _already_seeded(connection: Connection) -> bool:
    return bool(connection.execute(text("SELECT count(*) FROM site")).scalar_one())


def _set_site(connection: Connection, site_id: str) -> None:
    connection.execute(
        text("SELECT set_config('draupnir.site_id', :site_id, true)"), {"site_id": site_id}
    )


def _insert(connection: Connection, table: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    columns = list(rows[0])
    placeholders = ", ".join(f":{column}" for column in columns)
    statement = text(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"  # noqa: S608
    )
    connection.execute(statement, rows)


def write(dataset: dict[str, Any], url: str) -> None:
    """Write the dataset, respecting the row level security site scope.

    Order matters. The ledger goes in first and the projectors build `run`,
    then the artefacts, approvals and releases, from it, because all four are
    derived (RF-33); gate results reference runs, so they follow.
    """
    engine = create_engine(url, future=True)
    chains: dict[str, Chain] = dataset["chains"]

    with engine.begin() as connection:
        if _already_seeded(connection):
            msg = "the database already holds sites; seed against an empty schema"
            raise SystemExit(msg)
        _insert(connection, "site", dataset["sites"])
        _insert(connection, "source", dataset["sources"])
        _insert(connection, "plugin", dataset["plugins"])

    for site_id, chain in chains.items():
        with engine.begin() as connection:
            _set_site(connection, site_id)
            _insert(connection, "ledger_entry", chain.rows or [])
            RunProjection(connection, SiteScope(site_id)).rebuild()
            # Artefacts, approvals and releases, folded from the chain just
            # written, exactly as an append projects them (RF-33).
            ReleaseProjection(connection, SiteScope(site_id)).rebuild()
            # And the gate results, from the evaluations the chain records
            # (RF-41), rather than inserted beside it.
            GateResultProjection(connection, SiteScope(site_id)).rebuild()

    engine.dispose()


def projected(dataset: dict[str, Any]) -> release_record.Projected:
    """The artefacts, approvals and releases the seeded chains project to. RF-33.

    The rows the seed no longer writes, folded per site by the same function
    `ReleaseProjection` uses, so the summary and the tests describe what the
    database will hold rather than a list kept beside it.
    """
    artefacts: list[release_record.ProjectedArtefact] = []
    approvals: list[release_record.ProjectedApproval] = []
    published: list[release_record.ProjectedRelease] = []
    for chain in dataset["chains"].values():
        folded = release_record.fold(
            LedgerEntry(
                id=row["id"],
                site_id=row["site_id"],
                seq=row["seq"],
                prev_hash=row["prev_hash"],
                entry_hash=row["entry_hash"],
                ts=row["ts"],
                actor=row["actor"],
                subject_type=row["subject_type"],
                subject_id=row["subject_id"],
                transition=row["transition"],
                payload=json.loads(row["payload"]),
            )
            for row in chain.rows or []
        )
        artefacts.extend(folded.artefacts)
        approvals.extend(folded.approvals)
        published.extend(folded.releases)
    return release_record.Projected(
        artefacts=tuple(artefacts), approvals=tuple(approvals), releases=tuple(published)
    )


def projected_gate_results(dataset: dict[str, Any]) -> tuple[Any, ...]:
    """The gate results the seeded chains project to. RF-41.

    Folded per site by the function `GateResultProjection` uses, from the
    evaluations recorded on the chain, so the count describes the rows the
    database will hold rather than a list kept beside it.
    """
    rows: list[Any] = []
    for chain in dataset["chains"].values():
        rows.extend(
            gate_record.fold(
                LedgerEntry(**{**row, "payload": json.loads(row["payload"])})
                for row in chain.rows or []
            )
        )
    return tuple(rows)


def summarise(dataset: dict[str, Any]) -> str:
    """Return the one-line-per-entity summary printed after a seed."""
    chains: dict[str, Chain] = dataset["chains"]
    states = {row["state"] for row in dataset["runs"]}
    folded = projected(dataset)
    missing = {str(state) for state in RUN_PHASE_STATES} - states
    lines = [
        f"  sites          {len(dataset['sites']):>4}",
        f"  sources        {len(dataset['sources']):>4}",
        f"  runs           {len(dataset['runs']):>4}  resting in {len(states)} of "
        f"{len(RUN_PHASE_STATES)} run states",
        f"  artefacts      {len(folded.artefacts):>4}",
        # Folded from the chain, as `GateResultProjection` folds it (RF-41).
        f"  gate results   {len(projected_gate_results(dataset)):>4}",
        f"  approvals      {len(folded.approvals):>4}",
        f"  releases       {len(folded.releases):>4}",
        f"  plugins        {len(dataset['plugins']):>4}",
        f"  ledger entries {sum(chain.seq for chain in chains.values()):>4}"
        f"  ({', '.join(f'{name} {chain.seq}' for name, chain in chains.items())})",
    ]
    if missing:
        lines.append(f"  WARNING: run states not represented: {sorted(missing)}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Build and write the development dataset."""
    parser = argparse.ArgumentParser(description="Seed the DRAUPNIR development dataset")
    parser.add_argument("--url", default=None, help="Sync database URL; defaults to settings")
    parser.add_argument(
        "--dry-run", action="store_true", help="Build and summarise without writing"
    )
    args = parser.parse_args(argv)

    dataset = build()
    print("DRAUPNIR development dataset")
    print(summarise(dataset))

    if args.dry_run:
        print("dry run: nothing written")
        return 0

    write(dataset, args.url or get_settings().database_url_sync)
    print("seeded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
