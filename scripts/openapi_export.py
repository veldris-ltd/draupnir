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
    """Return the OpenAPI document as canonical JSON text.

    Built in the development posture, and that is safe rather than convenient
    (RF-23). `create_app` refuses to start without a way to authenticate a
    caller — RF-01's refusal, and a good one: a control plane that answers 401
    to everything reads as a broken deployment rather than as a missing
    setting. But *exporting the document serves no request*, so there is no
    caller to authenticate, and the refusal made `python tasks.py openapi`
    impossible to run anywhere without the operator setting an environment
    variable the task never mentioned. That includes CI stage 2.5 and the
    `clients-check` gate that calls it.

    Safe because the posture does not reach the document: the paths, the
    schemas and the security schemes are identical whether the application was
    built with `DRAUPNIR_DEV` or with an OIDC issuer. Exported both ways, the
    two files are byte for byte the same, and a test in
    `tests/unit/test_generated_artefacts.py` keeps it that way -- if a future
    change makes the document depend on how the caller is authenticated, that
    test fails rather than this quietly exporting one of two contracts.
    """
    import os

    from draupnir.api.app import create_app

    os.environ.setdefault("DRAUPNIR_DEV", "1")
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
    # LF on every platform (RF-23). Without this, `write_text` translates
    # each newline to `os.linesep` and a Windows run writes CRLF, so the
    # document a developer generates differs byte for byte from the one CI
    # generates -- while `git status` reports nothing, because the index
    # normalises. `clients-check` compares bytes and fails on it.
    args.output.write_text(document, encoding="utf-8", newline="\n")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
