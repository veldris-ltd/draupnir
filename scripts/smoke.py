"""Deployment smoke test: healthz, readyz, ledger chain verify.

SAD 11H stage 4. A failure here is what triggers the rollback step, so this
script exits non-zero and says why, rather than raising a traceback.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any

from sqlalchemy import create_engine, text

from draupnir.core.domain.ledger import GENESIS_HASH, compute_entry_hash
from draupnir.core.infrastructure.config import get_settings


def _get(url: str) -> tuple[int, Any]:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, None
    except (urllib.error.URLError, OSError, ValueError) as error:
        print(f"    {url}: {error}", file=sys.stderr)
        return 0, None


def check_health(base_url: str) -> bool:
    """Probe liveness."""
    status, body = _get(f"{base_url}/healthz")
    ok = status == 200 and isinstance(body, dict) and body.get("status") == "ok"
    print(f"  healthz  {'pass' if ok else 'FAIL'}  ({status})")
    return ok


def check_readiness(base_url: str) -> bool:
    """Probe readiness, and name the dependency that is not ready."""
    status, body = _get(f"{base_url}/readyz")
    ok = status == 200 and isinstance(body, dict) and body.get("status") == "ready"
    print(f"  readyz   {'pass' if ok else 'FAIL'}  ({status})")
    if isinstance(body, dict):
        for name, healthy in sorted((body.get("checks") or {}).items()):
            if not healthy:
                print(f"           dependency not ready: {name}", file=sys.stderr)
    return ok


def check_ledger(database_url: str) -> bool:
    """Verify every site chain end to end from the stored rows.

    This is the check that matters. A deployment that can serve HTTP but whose
    ledger no longer verifies is worse than one that is down.
    """
    engine = create_engine(database_url, future=True)
    intact = True
    try:
        with engine.connect() as connection:
            sites = [row[0] for row in connection.execute(text("SELECT id FROM site ORDER BY id"))]
            for site_id in sites:
                connection.execute(
                    text("SELECT set_config('draupnir.site_id', :site_id, false)"),
                    {"site_id": site_id},
                )
                rows = connection.execute(
                    text(
                        "SELECT seq, prev_hash, entry_hash, payload FROM ledger_entry "
                        "WHERE site_id = :site_id ORDER BY seq"
                    ),
                    {"site_id": site_id},
                ).all()

                expected_prev = GENESIS_HASH
                broken_at: int | None = None
                for index, (seq, prev_hash, entry_hash, payload) in enumerate(rows, start=1):
                    if seq != index or prev_hash != expected_prev:
                        broken_at = seq
                        break
                    if compute_entry_hash(prev_hash, payload) != entry_hash:
                        broken_at = seq
                        break
                    expected_prev = entry_hash

                if broken_at is None:
                    print(f"  ledger   pass  {site_id}: {len(rows)} entries verify")
                else:
                    intact = False
                    print(f"  ledger   FAIL  {site_id}: chain breaks at seq {broken_at}")
    except Exception as error:
        print(f"  ledger   FAIL  {error}", file=sys.stderr)
        intact = False
    finally:
        engine.dispose()
    return intact


def main(argv: list[str] | None = None) -> int:
    """Run every smoke check and return a process exit code."""
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--database-url", default=settings.database_url_sync)
    parser.add_argument(
        "--skip-ledger", action="store_true", help="Probe HTTP only, for a pre-migration check"
    )
    args = parser.parse_args(argv)

    print(f"DRAUPNIR smoke test against {args.base_url}")
    results = [check_health(args.base_url), check_readiness(args.base_url)]
    if not args.skip_ledger:
        results.append(check_ledger(args.database_url))

    if all(results):
        print("smoke: pass")
        return 0
    print("smoke: FAIL -- roll back", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
