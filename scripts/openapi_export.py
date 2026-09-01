"""Export the OpenAPI document from the FastAPI application.

Written deterministically (sorted keys, trailing newline) so that a diff
against the committed baseline means a real contract change and never
formatting noise.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "docs" / "api" / "openapi.json"


def render() -> str:
    """Return the OpenAPI document as canonical JSON text."""
    from draupnir.api.app import create_app

    document = create_app().openapi()
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Write the document, or check that the committed one matches."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if the file on disk differs from the application.",
    )
    args = parser.parse_args(argv)

    document = render()

    if args.check:
        if not args.output.exists():
            print(f"{args.output} does not exist; run `make openapi`", file=sys.stderr)
            return 1
        if args.output.read_text(encoding="utf-8") != document:
            print(
                f"{args.output} is stale. The application no longer matches the committed\n"
                "OpenAPI document. Run `make openapi` and commit the result.",
                file=sys.stderr,
            )
            return 1
        print(f"{args.output} is current")
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(document, encoding="utf-8")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
