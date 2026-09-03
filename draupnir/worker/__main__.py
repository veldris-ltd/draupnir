"""`python -m draupnir.worker`: the deployable unit of SAD 5.1.

Reads its configuration from the environment, ticks until it is asked to stop,
and exits zero when it is. A container's stop signal is the normal way it ends,
so `SIGTERM` is a clean exit rather than an error: an orchestrator that saw a
non-zero code on every rolling restart would report a fault every deployment.

`--once` runs a single tick and prints what it did, which is what a runbook step
and an integration test both want.
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
from pathlib import Path
from types import FrameType

from draupnir.worker.loop import TickReport, Worker, WorkerSettings


def _settings(args: argparse.Namespace) -> WorkerSettings:
    """The environment's settings, with the command line taking precedence."""
    from dataclasses import replace

    settings = WorkerSettings.from_environment()
    overrides: dict[str, object] = {}
    if args.site:
        overrides["site_id"] = args.site
    if args.database_url:
        overrides["database_url"] = args.database_url
    if args.interval is not None:
        overrides["interval"] = args.interval
    if args.scratch:
        overrides["scratch"] = Path(args.scratch)
    if args.no_duties:
        overrides["perform_duties"] = False
    return replace(settings, **overrides)  # type: ignore[arg-type]


def main(argv: list[str] | None = None) -> int:
    """Run the worker."""
    parser = argparse.ArgumentParser(prog="draupnir-worker", description=__doc__)
    parser.add_argument("--site", default="", help="the forge whose chain this worker moves")
    parser.add_argument("--database-url", default="")
    parser.add_argument("--interval", type=float, default=None, help="seconds between ticks")
    parser.add_argument("--scratch", default="", help="where runs write their artefacts")
    parser.add_argument("--once", action="store_true", help="run one tick and exit")
    parser.add_argument(
        "--ticks", type=int, default=None, help="run this many ticks and exit; implies --once"
    )
    parser.add_argument(
        "--no-duties",
        action="store_true",
        help="move runs but perform none of the periodic duties of SAD 11.3",
    )
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args(argv)

    logging.basicConfig(level=args.log_level.upper(), stream=sys.stderr, format="%(message)s")

    settings = _settings(args)
    worker = Worker(settings)

    def _stop(signum: int, frame: FrameType | None) -> None:
        del frame
        print(f"# stopping on signal {signum}", file=sys.stderr)
        worker.stop()

    for name in ("SIGINT", "SIGTERM"):
        handler = getattr(signal, name, None)
        if handler is not None:
            signal.signal(handler, _stop)

    iterations = args.ticks if args.ticks is not None else (1 if args.once else None)
    try:
        reports = worker.serve(iterations=iterations)
    finally:
        worker.close()

    if iterations is not None:
        print(json.dumps([report.as_payload() for report in reports], indent=2))
    else:
        _summarise(reports)
    return 0


def _summarise(reports: tuple[TickReport, ...]) -> None:
    """One closing line, for an operator reading a container's last output."""
    moved = sum(len(report.moved) for report in reports)
    alarms = sum(len(report.alarms) for report in reports)
    print(f"# {len(reports)} tick(s), {moved} transition(s), {alarms} alarm(s)", file=sys.stderr)


if __name__ == "__main__":  # pragma: no cover -- the entry point
    raise SystemExit(main())
