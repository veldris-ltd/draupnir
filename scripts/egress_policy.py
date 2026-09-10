"""Write the egress reconciliation as a build artefact. RF-E17.

Generated rather than maintained, for the reason the cryptographic inventory
is: a list kept by hand describes what somebody believed the system reached at
the time they last looked. `svalinn/egress.py` is what the broker actually
enforces, so it is what this reads.

Exits non-zero when the divergence between the two policies *changes*, not when
there is one. There is one today -- nine hosts -- and none of it can be fixed
from this repository, because section 10.5 lives in a controlled document. A
gate on "nothing is blocked" would be red from the day it was added and would
therefore be ignored, and a build that is always red is not a signal.

So the baseline is recorded in `site_egress.KNOWN_BLOCKED` with a reason for
each entry, and this fails on either kind of movement: a new blocked host,
which somebody introduced here, or a baseline entry that is no longer blocked,
which means the section was amended and the transcription is a revision behind.
"""

from __future__ import annotations

import sys
from pathlib import Path

from draupnir.svalinn import site_egress

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "docs" / "egress-policy.md"


def main() -> int:
    """Write the reconciliation, then report anything the router blocks."""
    rows = site_egress.reconcile()
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(site_egress.to_markdown(rows), encoding="utf-8", newline="\n")
    print(f"wrote {TARGET.relative_to(ROOT).as_posix()}: {len(rows)} hosts")

    stopped = site_egress.blocked(rows)
    if stopped:
        print()
        print(
            f"{len(stopped)} host(s) are reached by this programme and refused by "
            f"{site_egress.SOURCE_DOCUMENT} section {site_egress.SOURCE_SECTION}:"
        )
        for row in stopped:
            print(f"  {row.host:44} {', '.join(str(item) for item in row.consumers)}")

    orphans = site_egress.unclaimed(rows)
    if orphans:
        print()
        print(f"{len(orphans)} router entr(ies) that nothing here claims:")
        for row in orphans:
            print(f"  {row.host}")
        print("Not a fault. An entry nobody can justify is one nobody can remove.")

    return _gate(rows)


def _gate(rows: tuple[site_egress.Row, ...]) -> int:
    """Fail on movement in either direction. See the module docstring."""
    added = site_egress.unrecorded(rows)
    gone = site_egress.resolved(rows)
    if not added and not gone:
        return 0

    print()
    if added:
        print(f"{len(added)} NEW divergence(s), not in the recorded baseline:")
        for row in added:
            print(f"  {row.host:44} {', '.join(str(item) for item in row.consumers)}")
        print(
            "  Each of these leaves the host and is dropped at the router, so it "
            "presents as a timeout rather than as a refusal. Either the destination is "
            "not needed -- remove the call -- or section 10.5 is short of it, which is a "
            "change proposal against a controlled document and not an edit here. If it "
            "is the latter, add it to KNOWN_BLOCKED with the reason."
        )
    if gone:
        print(f"{len(gone)} baseline entr(ies) are no longer blocked: {', '.join(gone)}")
        print(
            "  That means section 10.5 was amended. Re-take the transcription in "
            "site_egress.py from the current revision before trusting anything else "
            "this report says, then drop the entries from KNOWN_BLOCKED."
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
