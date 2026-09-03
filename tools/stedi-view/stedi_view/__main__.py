"""Render the panel.

Three modes, all of them read only. There is no control on this view, so there
is no action on it that could fail while the network is down -- which is the
condition it is bought for.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime

from stedi_view import __version__
from stedi_view.readings import Status, all_readings

WIDTH = 46


def frame() -> str:
    """One rendering of the eight lines, plus a header and a footer."""
    taken = datetime.now(UTC).strftime("%H:%M:%S")
    readings = all_readings()
    lines = [
        "DRAUPNIR CON-A".ljust(WIDTH - 9) + taken,
        "-" * WIDTH,
        *[reading.render(WIDTH) for reading in readings],
        "-" * WIDTH,
    ]
    # The footer says what the panel is, because someone reading it during an
    # outage may never have seen it before and needs to know that the last two
    # lines reading `unreachable` is the expected picture rather than a second
    # fault.
    unreachable = [r.label for r in readings if r.status is Status.UNREACHABLE]
    lines.append(
        "Local readings only. No API dependency."
        if not unreachable
        else f"Local readings only. Unreachable: {', '.join(unreachable)}."
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Render once, or watch."""
    parser = argparse.ArgumentParser(prog="stedi-view", description=__doc__)
    parser.add_argument("--watch", action="store_true", help="Redraw every two seconds.")
    parser.add_argument("--json", action="store_true", help="Machine readable readings.")
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)

    if args.json:
        payload = [
            {"label": r.label, "value": r.value, "status": str(r.status)} for r in all_readings()
        ]
        sys.stdout.write(json.dumps(payload, indent=2) + "\n")
        return 0

    if not args.watch:
        sys.stdout.write(frame() + "\n")
        return 0

    try:
        while True:
            # `\033[H\033[2J` rather than a curses screen: the panel is a
            # framebuffer console and curses would need a terminfo database
            # that a minimal appliance image does not carry.
            sys.stdout.write("\033[H\033[2J" + frame() + "\n")
            sys.stdout.flush()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
