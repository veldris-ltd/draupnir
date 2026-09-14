"""Generate the TypeScript tier table from `draupnir/hamarr/tiers.py`. RF-35.

The API refuses a specification whose base is not its jurisdiction's tier's
(`hamarr.config.prepare`), and the console composed its default specification
with a base typed in by hand -- the Tier B base, for GBR, which is Tier A. Every
first dry run on the compose screen was a 422, and J2's submission journeys
failed from RF-11 until this.

So the console does not name a base. It takes the jurisdiction's tier and the
tier's base from this table, which is written from the table the API validates
against and compared by `clients-check` like every other generated client
file. A tier reassignment or a new base arrives in the console by regenerating,
and cannot arrive in one without the other.

The site is not here: `base_artefact` puts it in the address, it is deployment
configuration, and the console reads it from `/healthz`.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from draupnir.hamarr import tiers
from draupnir.interfaces.types import Tier

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "web" / "packages" / "api-client" / "src" / "generated" / "tiers.ts"

HEADER = """\
/* Generated tier table. Do not edit.
 *
 * Produced by `scripts/generate_ts_tiers.py` from `draupnir/hamarr/tiers.py`,
 * the table the API validates a specification against. The pipeline
 * regenerates this file and fails on any diff (AC-Q2).
 *
 * A client that composes a specification takes its base from here rather than
 * naming one, because the API refuses a base that is not the tier's (RF-35).
 */

/** A CIM-56 tier. */
export type Tier = {tiers};

/** The base model each tier trains against. SAD 13.5. */
export const BASE_FOR_TIER = {{
"""


def render() -> str:
    """Return the full text of the generated module."""
    # Refuse to write a table that does not enumerate the programme: a drifted
    # table copied into a client is the same silent wrong base, one step away.
    tiers.validate()

    lines = [HEADER.format(tiers=" | ".join(_quoted(str(tier)) for tier in Tier))]
    for tier in Tier:
        lines.append(f"  {tier}: {_quoted(str(tiers.BASE_FOR_TIER[tier]))},\n")
    lines.append("} as const satisfies Record<Tier, string>;\n\n")
    lines.append("/** Every CIM-56 jurisdiction's tier, by ISO 3166-1 alpha-3 code. */\n")
    lines.append("export const TIER_OF = {\n")
    for code in tiers.ALL:
        lines.append(f"  {code}: {_quoted(str(tiers.tier_of(code)))},\n")
    lines.append("} as const satisfies Record<string, Tier>;\n")
    return "".join(lines)


def _quoted(value: str) -> str:
    """A TypeScript string literal in the repository's Prettier style."""
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def main(argv: list[str] | None = None) -> int:
    """Write the tier table."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    args.output.write_text(render(), encoding="utf-8", newline="\n")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
