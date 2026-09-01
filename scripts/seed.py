"""Seed a realistic development dataset.

Produces, exactly as Prompt 0 specifies:

    2 sites, 6 sources, 12 runs across every state, 3 releases,
    400 ledger entries

The dataset is deterministic. Identifiers are UUIDv7 built from a fixed epoch
and a seeded stream, so two developers running `make dev` get byte-identical
databases and a screenshot in a bug report means something.

Two modelling notes, because the seed is where they first bite.

  * SAD 6.1 tabulates fourteen states. Four of them -- DRAFT,
    CORPUS_REGISTERED, LICENCE_CLEARED, CURATED -- describe a corpus before a
    run specification exists. `run.state` therefore covers the twelve states in
    `RUN_PHASE_STATES`, and `source.state` carries the two registration states
    that belong to the corpus alone. Twelve runs cover every run state exactly.
  * SAD 11C constraint 3 puts row level security on the site scoped tables, so
    the seed sets `draupnir.site_id` per site and writes each site's rows in its
    own transaction. If that variable were not set, every insert below would be
    refused, which is the point.
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

from draupnir.core.domain.identifiers import id_at
from draupnir.core.domain.ledger import GENESIS_HASH, compute_entry_hash
from draupnir.core.domain.states import RUN_PHASE_STATES, RunState
from draupnir.core.infrastructure.config import get_settings

SEED = 20260901
EPOCH = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)

TARGET_LEDGER_ENTRIES = 400
TARGET_RUNS = 12
TARGET_SOURCES = 6
TARGET_RELEASES = 3


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
    ("cim-aus-v0.1", "adapter", RunState.AWAITING_APPROVAL, "brokkr"),
    ("cim-gbr-v0.1", "adapter", RunState.RELEASED, "sindri"),
    ("cim-fra-v0.1", "adapter", RunState.QUARANTINED, "sindri"),
)

GATES = ("E1", "E2", "E3", "E4", "E5", "E6")
GATE_SUITE_VERSION = "raun-suite/2026.02"

ACTORS = (
    "curator@veldris.internal",
    "operator@veldris.internal",
    "approver@veldris.internal",
    "system:motsognir",
    "system:raun",
    "system:gullinbursti",
)

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
        self._offset = 0

    def next(self, moment: datetime) -> UUID:
        """Return a fresh identifier stamped at `moment`."""
        self._offset += 1
        return id_at(moment, self._rng.randbytes(10))


def fake_sha256(rng: random.Random, label: str) -> str:
    """A stable, obviously synthetic digest. Real hashes come from HODD."""
    return hashlib.sha256(f"{label}:{rng.random()}".encode()).hexdigest()


def _minutes(rng: random.Random, low: int, high: int) -> timedelta:
    return timedelta(minutes=rng.randint(low, high))


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def _already_seeded(connection: Connection) -> bool:
    return bool(connection.execute(text("SELECT count(*) FROM site")).scalar_one())


def _set_site(connection: Connection, site_id: str) -> None:
    """Set the row level security site variable for this transaction."""
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

    chains = {site["id"]: Chain(site_id=str(site["id"])) for site in SITES}
    clock = {site["id"]: EPOCH for site in SITES}

    def tick(site_id: str, low: int = 15, high: int = 240) -> datetime:
        clock[site_id] = clock[site_id] + _minutes(rng, low, high)
        return clock[site_id]

    # -- sources ------------------------------------------------------------
    sources: list[dict[str, Any]] = []
    for index, spec in enumerate(SOURCES):
        moment = EPOCH + timedelta(hours=index)
        source_id = ids.next(moment)
        sources.append(
            {
                "id": source_id,
                "jurisdiction": spec["jurisdiction"],
                "url": spec["url"],
                "licence_spdx": spec["licence_spdx"],
                "attribution_required": spec["attribution_required"],
                "retrieved_at": moment,
                "sha256": fake_sha256(rng, str(spec["url"])),
                "personal_data": spec["personal_data"],
                "dpia_ref": spec["dpia_ref"],
                "residency_constraint": spec["residency_constraint"],
                "state": str(spec["state"]),
            }
        )
        # A source registers against the site that ingested it.
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
                "sha256": sources[-1]["sha256"],
                "personal_data": spec["personal_data"],
                "jurisdiction": spec["jurisdiction"],
            },
            entry_id=ids.next(clock[site_id]),
        )

    # -- runs, artefacts, gates, approvals, releases ------------------------
    runs: list[dict[str, Any]] = []
    artefacts: list[dict[str, Any]] = []
    gate_results: list[dict[str, Any]] = []
    approvals: list[dict[str, Any]] = []
    releases: list[dict[str, Any]] = []

    for index, (name, kind, state, site_id) in enumerate(RUN_PLAN):
        started = EPOCH + timedelta(days=2 + index, hours=rng.randint(0, 8))
        run_id = ids.next(started)
        reached = state in (
            RunState.TRAINING,
            RunState.TRAINED,
            RunState.FAILED,
            RunState.EVALUATING,
            RunState.MERGED,
            RunState.QUANTISED,
            RunState.AWAITING_APPROVAL,
            RunState.RELEASED,
            RunState.QUARANTINED,
        )
        finished = state not in (
            RunState.TRAINING,
            RunState.QUEUED,
            RunState.DRAFT,
            RunState.CURATED,
        )
        runs.append(
            {
                "id": run_id,
                "site_id": site_id,
                "name": name,
                "spec_hash": fake_sha256(rng, f"spec:{name}"),
                "kind": kind,
                "state": str(state),
                "started_at": started if reached else None,
                "ended_at": started + _minutes(rng, 90, 900) if finished else None,
                "scheduler_job_id": f"{rng.randint(100000, 999999)}" if reached else None,
                "node": rng.choice(("dvalin", "durin", "dain")) if reached else None,
                "retry_count": 1 if state in (RunState.FAILED, RunState.EVALUATING) else 0,
            }
        )

        # Ledger: one entry per transition the run has actually made.
        lifecycle = _path_to(state)
        for step_from, step_to in pairwise(lifecycle):
            chains[site_id].append(
                ts=tick(site_id, 20, 300),
                actor=_actor_for(step_to, rng),
                subject_type="run",
                subject_id=str(run_id),
                transition=f"{step_from}->{step_to}",
                payload=_payload_for(step_to, name, rng),
                entry_id=ids.next(clock[site_id]),
            )

        # Gates are evaluated from EVALUATING onwards.
        if state in (
            RunState.EVALUATING,
            RunState.MERGED,
            RunState.QUANTISED,
            RunState.AWAITING_APPROVAL,
            RunState.RELEASED,
        ):
            for gate in GATES:
                baseline = round(rng.uniform(0.58, 0.74), 4)
                value = round(baseline + rng.uniform(-0.01, 0.06), 4)
                gate_results.append(
                    {
                        "id": ids.next(started),
                        "run_id": run_id,
                        "gate": gate,
                        "suite_version": GATE_SUITE_VERSION,
                        "value": value,
                        "baseline_value": baseline,
                        "margin": round(value - baseline, 4),
                        "passed": value >= baseline,
                        "evaluated_at": started + timedelta(hours=6),
                    }
                )

        # Artefacts. Every run that trained produced at least a checkpoint.
        if state in (RunState.TRAINED, RunState.EVALUATING, RunState.MERGED):
            artefacts.append(
                _artefact(ids, rng, site_id, run_id, "adapter", f"adapters/{name}", started)
            )
        if state in (RunState.QUANTISED, RunState.AWAITING_APPROVAL, RunState.RELEASED):
            artefacts.append(
                _artefact(ids, rng, site_id, run_id, "quantised", f"models/{name}/nvfp4", started)
            )

        # Approvals for everything that reached a decision.
        if state in (RunState.AWAITING_APPROVAL, RunState.RELEASED, RunState.QUARANTINED):
            decided = started + timedelta(days=1)
            approvals.append(
                {
                    "id": ids.next(decided),
                    "subject_id": run_id,
                    "approver": "approver@veldris.internal",
                    "decision": "REJECTED" if state is RunState.QUARANTINED else "APPROVED",
                    "reason": "Licence policy refusal on a constituent source"
                    if state is RunState.QUARANTINED
                    else None,
                    "signature": fake_sha256(rng, f"sig:{name}"),
                    "sole_approver_exception": False,
                    "decided_at": decided,
                }
            )

    # -- releases: three, each bound to an approval and a quantised artefact -
    quantised = [artefact for artefact in artefacts if artefact["kind"] == "quantised"]
    approved = [approval for approval in approvals if approval["decision"] == "APPROVED"]
    for index in range(TARGET_RELEASES):
        artefact = quantised[index % len(quantised)]
        approval = approved[index % len(approved)]
        published = EPOCH + timedelta(days=20 + index * 3)
        release_id = ids.next(published)
        base = f"hodd://{artefact['site_id']}/releases/{release_id}"
        releases.append(
            {
                "id": release_id,
                "artefact_id": artefact["id"],
                "approval_id": approval["id"],
                "model_card_uri": f"{base}/model-card.md",
                "sbom_uri": f"{base}/sbom.cdx.json",
                "lineage_uri": f"{base}/lineage.json",
                "training_summary_uri": f"{base}/article-53-training-summary.md",
                "copyright_policy_uri": f"{base}/article-53-copyright-policy.md",
                "signature": fake_sha256(rng, f"release:{release_id}"),
                "anchored_at": published + timedelta(minutes=11),
                "published_at": published,
            }
        )
        site_id = str(artefact["site_id"])
        chains[site_id].append(
            ts=tick(site_id, 10, 60),
            actor="approver@veldris.internal",
            subject_type="release",
            subject_id=str(release_id),
            transition="AWAITING_APPROVAL->RELEASED",
            payload={
                "artefact_id": str(artefact["id"]),
                "approval_id": str(approval["id"]),
                "formats": ["nvfp4", "gguf-q4km", "mlx4"],
                "anchored": True,
            },
            entry_id=ids.next(clock[site_id]),
        )

    # -- pad the chains to exactly 400 entries ------------------------------
    written = sum(chain.seq for chain in chains.values())
    order = [str(site["id"]) for site in SITES]
    while written < TARGET_LEDGER_ENTRIES:
        site_id = order[written % len(order)]
        subject_type, transition, actor = BACKGROUND_EVENTS[written % len(BACKGROUND_EVENTS)]
        chain = chains[site_id]
        chains[site_id].append(
            ts=tick(site_id, 5, 90),
            actor=actor,
            subject_type=subject_type,
            subject_id=site_id if subject_type == "site" else str(ids.next(clock[site_id])),
            transition=transition,
            payload={
                "seq": chain.seq + 1,
                "head": chain.prev_hash[:16],
                "note": "operational event",
            },
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
        "artefacts": artefacts,
        "gate_results": gate_results,
        "approvals": approvals,
        "releases": releases,
        "plugins": plugins,
        "chains": chains,
    }


def _artefact(
    ids: Ids,
    rng: random.Random,
    site_id: str,
    run_id: UUID,
    kind: str,
    path: str,
    moment: datetime,
) -> dict[str, Any]:
    return {
        "id": ids.next(moment),
        "site_id": site_id,
        "locality": [site_id],
        "kind": kind,
        "uri": f"hodd://{site_id}/{path}",
        "sha256_manifest": fake_sha256(rng, path),
        "size": rng.randint(2 * 10**8, 9 * 10**9),
        "created_from_run": run_id,
        "immutable_at": moment + timedelta(hours=8),
    }


def _path_to(state: RunState) -> list[str]:
    """Return the lifecycle path a run in `state` has walked, per SAD 6.1."""
    spine = [
        RunState.DRAFT,
        RunState.CURATED,
        RunState.QUEUED,
        RunState.TRAINING,
        RunState.TRAINED,
        RunState.EVALUATING,
        RunState.MERGED,
        RunState.QUANTISED,
        RunState.AWAITING_APPROVAL,
        RunState.RELEASED,
    ]
    if state is RunState.FAILED:
        return [
            str(item) for item in (*spine[: spine.index(RunState.TRAINING) + 1], RunState.FAILED)
        ]
    if state is RunState.QUARANTINED:
        return [
            str(item)
            for item in (
                *spine[: spine.index(RunState.AWAITING_APPROVAL) + 1],
                RunState.QUARANTINED,
            )
        ]
    return [str(item) for item in spine[: spine.index(state) + 1]]


def _actor_for(state: str, rng: random.Random) -> str:
    mapping = {
        "QUEUED": "system:motsognir",
        "TRAINING": "system:motsognir",
        "TRAINED": "system:hamarr",
        "FAILED": "system:hamarr",
        "EVALUATING": "system:raun",
        "MERGED": "system:brisingamen",
        "QUANTISED": "system:skidbladnir",
        "AWAITING_APPROVAL": "system:gleipnir",
        "RELEASED": "approver@veldris.internal",
        "QUARANTINED": "approver@veldris.internal",
    }
    return mapping.get(state, rng.choice(ACTORS))


def _payload_for(state: str, name: str, rng: random.Random) -> dict[str, Any]:
    if state == "TRAINING":
        return {"scheduler_job_id": str(rng.randint(100000, 999999)), "partition": "adapters"}
    if state == "TRAINED":
        return {"steps": rng.randint(1200, 4800), "final_loss": round(rng.uniform(0.7, 1.4), 4)}
    if state == "FAILED":
        return {"exit_code": rng.choice((1, 137)), "reason": "CUDA out of memory on rank 0"}
    if state == "EVALUATING":
        return {"suite_version": GATE_SUITE_VERSION, "gates": list(GATES)}
    if state == "MERGED":
        return {"method": "ties", "weights": [0.6, 0.4]}
    if state == "QUANTISED":
        return {"formats": ["nvfp4", "gguf-q4km", "mlx4"]}
    return {"run": name}


def write(dataset: dict[str, Any], url: str) -> None:
    """Write the dataset, respecting the row level security site scope."""
    engine = create_engine(url, future=True)
    chains: dict[str, Chain] = dataset["chains"]

    with engine.begin() as connection:
        if _already_seeded(connection):
            msg = "the database already holds sites; seed against an empty schema"
            raise SystemExit(msg)
        _insert(connection, "site", dataset["sites"])
        _insert(connection, "source", dataset["sources"])
        _insert(connection, "plugin", dataset["plugins"])

    for site in dataset["sites"]:
        site_id = site["id"]
        with engine.begin() as connection:
            _set_site(connection, site_id)
            _insert(
                connection,
                "run",
                [row for row in dataset["runs"] if row["site_id"] == site_id],
            )
            _insert(
                connection,
                "artefact",
                [row for row in dataset["artefacts"] if row["site_id"] == site_id],
            )

    with engine.begin() as connection:
        _insert(connection, "gate_result", dataset["gate_results"])
        _insert(connection, "approval", dataset["approvals"])
        _insert(connection, "release", dataset["releases"])

    for site_id, chain in chains.items():
        with engine.begin() as connection:
            _set_site(connection, site_id)
            _insert(connection, "ledger_entry", chain.rows or [])

    engine.dispose()


def summarise(dataset: dict[str, Any]) -> str:
    """Return the one-line-per-entity summary printed after a seed."""
    chains: dict[str, Chain] = dataset["chains"]
    states = {row["state"] for row in dataset["runs"]}
    missing = {str(state) for state in RUN_PHASE_STATES} - states
    lines = [
        f"  sites          {len(dataset['sites']):>4}",
        f"  sources        {len(dataset['sources']):>4}",
        f"  runs           {len(dataset['runs']):>4}  covering {len(states)} of "
        f"{len(RUN_PHASE_STATES)} run states",
        f"  artefacts      {len(dataset['artefacts']):>4}",
        f"  gate results   {len(dataset['gate_results']):>4}",
        f"  approvals      {len(dataset['approvals']):>4}",
        f"  releases       {len(dataset['releases']):>4}",
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
