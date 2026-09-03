"""Two operator commands over the ledger: verify a chain, rebuild a projection.

Both are in the runbook, and both exist because the alternative is an operator
with `psql`. The ledger is append only and `run` is derived from it, so the
only two operations an operator ever legitimately performs on them are reading
the chain and replaying it.

Neither writes to `ledger_entry`. There is no command here that can, and the
table refuses `UPDATE` and `DELETE` by trigger besides.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

from draupnir.core.domain.sites import SiteScope
from draupnir.core.infrastructure.repositories import (
    LedgerRepository,
    RunProjection,
    set_site_scope,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_URL = "postgresql+psycopg://draupnir:draupnir@127.0.0.1:5432/draupnir"


def _sites(url: str) -> list[str]:
    """Every registered site. Reading the registry is not a scoped query."""
    engine = create_engine(url, future=True)
    try:
        with engine.connect() as connection:
            return [row[0] for row in connection.execute(text("SELECT id FROM site ORDER BY id"))]
    finally:
        engine.dispose()


def verify(url: str, site_ids: list[str], *, report: bool) -> int:
    """Verify each site's chain. Returns non-zero on the first divergence."""
    engine = create_engine(url, future=True)
    findings: list[dict[str, object]] = []
    try:
        with engine.connect() as connection:
            for site_id in site_ids:
                scope = SiteScope(site_id)
                set_site_scope(connection, scope, local=False)
                ledger = LedgerRepository(connection, scope)
                length = ledger.length()
                divergent = ledger.verify_chain()
                findings.append(
                    {
                        "site": site_id,
                        "entries": length,
                        "intact": divergent is None,
                        "divergentSeq": divergent,
                        "entryHashAtDivergence": (
                            ledger.entry_hash_at(divergent) if divergent else None
                        ),
                    }
                )
    finally:
        engine.dispose()

    for finding in findings:
        if finding["intact"]:
            print(f"  {finding['site']}: {finding['entries']} entries, chain intact")
        else:
            print(
                f"  {finding['site']}: DIVERGENT at seq {finding['divergentSeq']} "
                f"of {finding['entries']}. Do not append. Runbook section 6."
            )

    if report:
        print(json.dumps(findings, indent=2, sort_keys=True))

    return 0 if all(finding["intact"] for finding in findings) else 1


def rebuild(url: str, site_ids: list[str]) -> int:
    """Replay each site's chain into the run registry.

    Idempotent: the fold is pure and the write is a full replacement, so
    running it twice produces identical table contents.
    """
    engine = create_engine(url, future=True)
    try:
        with engine.begin() as connection:
            for site_id in site_ids:
                scope = SiteScope(site_id)
                set_site_scope(connection, scope, local=False)
                report = RunProjection(connection, scope).rebuild()
                print(
                    f"  {site_id}: {report.entries_read} entries read, "
                    f"{report.rows_written} rows written, head at seq {report.last_seq}"
                )
    finally:
        engine.dispose()
    return 0


def main(argv: list[str] | None = None) -> int:
    """Verify or rebuild."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("verify", "rebuild"))
    parser.add_argument("--site", action="append", dest="sites", help="defaults to every site")
    parser.add_argument(
        "--database-url", default=os.environ.get("DRAUPNIR_DATABASE_URL_SYNC", DEFAULT_URL)
    )
    parser.add_argument("--report", action="store_true", help="verify: also print JSON")
    args = parser.parse_args(argv)

    sites = args.sites or _sites(args.database_url)
    if not sites:
        print("no site is registered; there is no chain to read", file=sys.stderr)
        return 1

    if args.action == "verify":
        return verify(args.database_url, sites, report=args.report)
    return rebuild(args.database_url, sites)


if __name__ == "__main__":
    raise SystemExit(main())
