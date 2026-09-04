"""Three operator commands over the HODD vault: status, initialise, reconcile.

`reconcile` is the one SAD 11.2 row 4 names: "restore NFS, run reconciliation".
It is the step after an operator has put the mount back, and it does what the
row's behaviour column promises -- "running jobs writing to local scratch
continue and stage on recovery".

It reports and changes nothing unless `--apply` is given. The moment after an
outage is the moment to read what happened before touching it.

    python scripts/vault_admin.py status
    python scripts/vault_admin.py reconcile              # dry run
    python scripts/vault_admin.py reconcile --apply      # stage, and record it

What it expects to find is read from the chain, not from the disk: a run that
reached TRAINED recorded its checkpoint's digest, one that reached QUANTISED
recorded the merge and every format it built. Those digests are what scratch is
checked against, and an artefact whose bytes do not match the digest the chain
recorded is reported rather than staged (AC-S8).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

from draupnir.core.domain.ledger import LedgerEntry
from draupnir.core.domain.sites import SiteScope
from draupnir.core.domain.states import RunState
from draupnir.core.infrastructure.orchestration import for_connection
from draupnir.core.infrastructure.repositories import LedgerRepository, set_site_scope
from draupnir.hodd import reconcile as hodd
from draupnir.hodd.ingest import Ingestor
from draupnir.hodd.register import LicenceRegister
from draupnir.hodd.stores import PosixStoreDriver, StoreError, artefact_uri

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_URL = "postgresql+psycopg://draupnir:draupnir@127.0.0.1:5432/draupnir"
DEFAULT_VAULT = "/mnt/hodd"
DEFAULT_SCRATCH = ROOT / "build" / "worker"

#: Which ledger transitions carry an artefact digest, what kind of artefact it
#: is, and what the job called the file in scratch. One table rather than a walk
#: of conditionals, so a new artefact kind is a row here and nothing else.
ARTEFACTS: tuple[tuple[str, str, str, str], ...] = (
    (
        f"{RunState.TRAINING}->{RunState.TRAINED}",
        "checkpoint_sha256",
        "adapter",
        "adapter.safetensors",
    ),
    (
        f"{RunState.MERGED}->{RunState.QUANTISED}",
        "merged_sha256",
        "merged",
        "merged.safetensors",
    ),
)

#: The quantised builds are a mapping of format to digest rather than one
#: field, so they are read separately.
FORMATS_TRANSITION = f"{RunState.MERGED}->{RunState.QUANTISED}"
FORMATS_FIELD = "formats_built"


def _store(vault: Path, site: str) -> PosixStoreDriver:
    return PosixStoreDriver(root=vault, local_site=site)


def _ingestor(store: PosixStoreDriver) -> Ingestor:
    return Ingestor(store, LicenceRegister())


def _sites(url: str) -> list[str]:
    """Every registered site. Reading the registry is not a scoped query."""
    engine = create_engine(url, future=True)
    try:
        with engine.connect() as connection:
            return [row[0] for row in connection.execute(text("SELECT id FROM site ORDER BY id"))]
    finally:
        engine.dispose()


def expected_from(entries: Iterator[LedgerEntry], site: str, scratch: Path) -> list[hodd.Expected]:
    """Every artefact the chain says the vault should hold, for one site.

    Read forward, so a later entry about the same run replaces an earlier one:
    a requeued run trains twice and the second checkpoint is the one its
    lineage refers to.
    """
    found: dict[str, hodd.Expected] = {}

    def remember(run_id: str, kind: str, digest: str, filename: str, name: str = "") -> None:
        """Record one expectation.

        `name` addresses within a kind -- the three release formats of one run
        -- and `filename` is what the job called the file it left in scratch.
        """
        uri = artefact_uri(site, kind, run_id, name)
        candidate = scratch / run_id / filename
        found[uri] = hodd.Expected(
            uri=uri,
            sha256=digest,
            kind=kind,
            run_id=run_id,
            scratch=candidate if candidate.is_file() else None,
        )

    for entry in entries:
        payload = entry.payload if isinstance(entry.payload, dict) else {}
        for transition, field, kind, filename in ARTEFACTS:
            digest = payload.get(field)
            if entry.transition == transition and isinstance(digest, str) and digest:
                remember(entry.subject_id, kind, digest, filename)
        if entry.transition == FORMATS_TRANSITION:
            built = payload.get(FORMATS_FIELD)
            if isinstance(built, dict):
                for fmt, digest in sorted(built.items()):
                    if isinstance(digest, str) and digest:
                        remember(entry.subject_id, "quantised", digest, f"{fmt}.bin", name=fmt)

    return [found[uri] for uri in sorted(found)]


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def status(vault: Path, site: str) -> int:
    """Say whether the vault is there, whether it is the vault, and how full."""
    store = _store(vault, site)
    marker = hodd.marker_of(store)

    print(f"vault      {vault}")
    if not vault.is_dir():
        print("state      NOT MOUNTED")
        print("           New runs refuse to plan (SAD 11.2 row 4). Restore the mount,")
        print("           then run `vault_admin.py reconcile`.")
        return 1
    if marker is None:
        print("state      PRESENT BUT NOT A VAULT")
        print(f"           {vault} exists and holds no {hodd.VAULT_MARKER}. This is a")
        print("           directory where the vault should be, which is worse than the")
        print("           vault being absent: writes would land on local disk.")
        return 1

    print("state      MOUNTED")
    print(f"site       {marker.get('site', '?')}")
    print(f"since      {marker.get('initialisedAt', '?')}")
    try:
        free, total = store.free_bytes(), store.total_bytes()
    except StoreError as refusal:
        print(f"capacity   unreadable: {refusal}")
        return 1
    used = (total - free) / total if total else 0.0
    print(f"capacity   {used:.0%} used, {free / 1e9:.1f} GB free of {total / 1e9:.1f} GB")
    return 0


# ---------------------------------------------------------------------------
# initialise
# ---------------------------------------------------------------------------


def initialise(vault: Path, site: str) -> int:
    """Write the marker that makes a mounted directory this site's vault."""
    store = _store(vault, site)
    try:
        marker = hodd.initialise(store)
    except StoreError as refusal:
        print(f"refused: {refusal}", file=sys.stderr)
        return 1
    print(f"{marker} written; {vault} is now the vault for {site}")
    return 0


# ---------------------------------------------------------------------------
# reconcile
# ---------------------------------------------------------------------------


def run_reconcile(
    url: str,
    vault: Path,
    site: str,
    scratch: Path,
    *,
    apply: bool,
    actor: str,
    as_json: bool,
) -> int:
    """Compare the vault against the chain, stage what belongs, and report."""
    store = _store(vault, site)
    ingestor = _ingestor(store)
    scope = SiteScope(site)
    engine = create_engine(url, future=True)

    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                set_site_scope(connection, scope, local=False)
                expected = expected_from(
                    LedgerRepository(connection, scope).stream(), site, scratch
                )
            finally:
                transaction.rollback()

            try:
                report = hodd.reconcile(
                    store,
                    ingestor,
                    expected,
                    known_uris=hodd.known(expected),
                    apply=apply,
                    now=datetime.now(UTC),
                )
            except StoreError as refusal:
                print(f"refused: {refusal}", file=sys.stderr)
                return 1

            if apply:
                _record(connection, scope, report, actor=actor)

        if as_json:
            print(json.dumps(report.as_payload(), indent=2))
        else:
            for line in hodd.describe(report):
                print(line)
    finally:
        engine.dispose()

    # Non-zero while anything still needs a person. A reconciliation that
    # exited zero with a diverged artefact in it would be a green light over
    # the one finding that has to be read.
    return 0 if report.settled else 2


def _record(connection: Any, scope: SiteScope, report: hodd.Report, *, actor: str) -> None:
    """Put the reconciliation in the chain: one entry per staging, and a summary.

    Staging an artefact into the vault is a fact about that artefact, and the
    reconciliation having been run at all is a fact an operator will later need
    to establish. Both go through the orchestrator, which is the one write path.
    """
    transaction = connection.begin()
    try:
        orchestrator = for_connection(connection, scope, actor=actor)
        for finding in report.of(hodd.Outcome.STAGED):
            orchestrator.record(
                subject_type="artefact",
                subject_id=finding.expected.sha256,
                transition=hodd.STAGED,
                payload=finding.as_payload(),
            )
        orchestrator.record(
            subject_type=hodd.VAULT_SUBJECT,
            subject_id=scope.site_id,
            transition=hodd.RECONCILED,
            payload=report.as_payload(),
        )
    except Exception:
        transaction.rollback()
        raise
    transaction.commit()


def main(argv: list[str] | None = None) -> int:
    """Status, initialise or reconcile."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "initialise", "reconcile"))
    parser.add_argument("--site", default=os.environ.get("DRAUPNIR_SITE_ID", "sindri"))
    parser.add_argument(
        "--vault",
        type=Path,
        default=Path(os.environ.get("DRAUPNIR_VAULT_ROOT", DEFAULT_VAULT)),
        help="the vault's mount point",
    )
    parser.add_argument(
        "--scratch",
        type=Path,
        default=Path(os.environ.get("DRAUPNIR_WORKER_SCRATCH", str(DEFAULT_SCRATCH))),
        help="where running jobs wrote, and where recovery stages from",
    )
    parser.add_argument(
        "--database-url", default=os.environ.get("DRAUPNIR_DATABASE_URL_SYNC", DEFAULT_URL)
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="reconcile: stage what belongs and record it, rather than only reporting",
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--actor", default="operator@veldris.internal")
    args = parser.parse_args(argv)

    if args.action == "status":
        return status(args.vault, args.site)
    if args.action == "initialise":
        return initialise(args.vault, args.site)

    known = _sites(args.database_url)
    if known and args.site not in known:
        print(f"{args.site} is not a registered site; known: {', '.join(known)}", file=sys.stderr)
        return 1
    return run_reconcile(
        args.database_url,
        args.vault,
        args.site,
        args.scratch,
        apply=args.apply,
        actor=args.actor,
        as_json=args.as_json,
    )


if __name__ == "__main__":
    raise SystemExit(main())
