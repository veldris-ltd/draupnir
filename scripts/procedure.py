"""Run Procedures M1 to M10 against a live stack, in one command.

AC-F12: "The complete Procedure M1 to M10 sequence from VLD-INF-SINDRI-001
executes end to end for one jurisdiction with no manual shell step."

This is the operator's spelling of that. `make procedure` runs it against the
development stack; it takes no input between steps, asks no question, and stops
at the first refusal with the refusal's own words.

It writes an evidence file, because a demonstration nobody can read afterwards
is a demonstration that happened once. `docs/acceptance/AC-F12.md` quotes it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

import draupnir_local_subprocess
from draupnir.core.domain.sites import SiteScope
from draupnir.core.infrastructure.orchestration import for_connection
from draupnir.procedures import Procedure, ProcedureError, run
from draupnir.procedures.sindri import STEPS, restore_writable

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_URL = "postgresql+psycopg://draupnir:draupnir@127.0.0.1:5432/draupnir"
EVIDENCE = ROOT / "docs" / "acceptance" / "evidence" / "procedure-m1-m10.json"


def _scheduler() -> Any:
    """The schedule driver this run places work through.

    The local subprocess driver, because that is what a machine without an
    estate has. On the estate the same code path resolves `motsognir.slurm/v1`
    from the specification's `placement.driver`, which is why the procedure
    takes the driver as an argument rather than importing one.
    """
    return draupnir_local_subprocess.driver


def _ensure_site(url: str, site_id: str) -> None:
    """Register the site if the database does not know it yet.

    Every scoped row has a foreign key to `site`, so a procedure run against an
    unseeded database would fail on its first append with a constraint error
    that says nothing about what is missing.
    """
    engine = create_engine(url, future=True)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO site (id, name, location, timezone, control_plane_uri, "
                    "anchor_state) VALUES (:id, :name, 'Belfast', 'Europe/London', "
                    "'https://sindri.veldris.internal', 'ANCHORED') "
                    "ON CONFLICT (id) DO NOTHING"
                ),
                {"id": site_id, "name": site_id.capitalize()},
            )
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    """Run the procedure and report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jurisdiction", default="GBR", help="ISO 3166-1 alpha-3, e.g. GBR")
    parser.add_argument("--site", default="sindri")
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DRAUPNIR_DATABASE_URL_SYNC", DEFAULT_URL),
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        default=ROOT / "build" / "procedure",
        help="where the procedure writes its corpora and artefacts",
    )
    parser.add_argument("--evidence", type=Path, default=EVIDENCE)
    parser.add_argument("--actor", default="operator@veldris.internal")
    args = parser.parse_args(argv)

    _ensure_site(args.database_url, args.site)

    # A previous run left its raw corpus read only, which is the control AC-F3
    # asks for and also what stops the next run writing into the same place. A
    # demonstration that can only be run once is not one anybody will run.
    workdir = args.workdir / args.jurisdiction
    restore_writable(workdir)

    started = datetime.now(UTC)
    engine = create_engine(args.database_url, future=True)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            procedure = Procedure(
                orchestrator=for_connection(connection, SiteScope(args.site), actor=args.actor),
                workdir=workdir,
                jurisdiction=args.jurisdiction,
                # What the retrieval varies. Without it a second run of the
                # same jurisdiction produces a byte-identical corpus, a
                # byte-identical specification and therefore the same run
                # identity -- which AC-F2 refuses, correctly, and which would
                # make this demonstration runnable exactly once.
                corpus_seed=started.isoformat(),
            )
            try:
                results = run(procedure, _scheduler())
            except ProcedureError as error:
                transaction.rollback()
                print(f"refused: {error}", file=sys.stderr)
                return 1
            transaction.commit()
    finally:
        engine.dispose()

    width = max(len(title) for _, title, _, _ in STEPS)
    for result in results:
        state = str(result.state) if result.state else "-"
        print(f"  {result.id:<3} {result.title:<{width}}  {state:<18} {result.seconds:6.2f}s")

    record: dict[str, Any] = {
        "criterion": "AC-F12",
        "jurisdiction": args.jurisdiction,
        "site": args.site,
        "runId": str(procedure.run_id),
        "model": procedure.model,
        "startedAt": started.isoformat(),
        "endedAt": datetime.now(UTC).isoformat(),
        "executor": procedure.executor,
        "steps": [result.as_payload() for result in results],
        "artefacts": dict(sorted(procedure.artefacts.items())),
        "renderedPlans": procedure.plans,
    }
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(f"\n  run {procedure.run_id} released; evidence in {args.evidence.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
