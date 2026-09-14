"""The console's tier table is the API's. RF-35.

The compose screen's default specification named the Tier B base for GBR, and
the API refused it. The console now derives the base from a table generated
from `draupnir/hamarr/tiers.py`; these tests hold the generated table to the
one the API validates against, and the console's address to `base_artefact`'s.
The dry run of the default itself is J2's, in stage 2.7.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

if str(REPO_ROOT) not in sys.path:  # pragma: no cover - import plumbing
    sys.path.insert(0, str(REPO_ROOT))

from draupnir.hamarr import config, tiers  # noqa: E402
from scripts import generate_ts_tiers  # noqa: E402

GENERATED = REPO_ROOT / "web" / "packages" / "api-client" / "src" / "generated" / "tiers.ts"
CONSOLE_DEFAULT = REPO_ROOT / "web" / "apps" / "console" / "src" / "specification.ts"


def test_the_committed_table_is_what_the_generator_writes() -> None:
    assert GENERATED.read_bytes().replace(b"\r\n", b"\n") == generate_ts_tiers.render().encode()


def test_the_table_assigns_every_jurisdiction_its_tier_and_every_tier_its_base() -> None:
    text = generate_ts_tiers.render()

    assigned = dict(re.findall(r"^  ([A-Z]{3}): '([AB])',$", text, flags=re.MULTILINE))
    bases = dict(re.findall(r"^  ([AB]): '([^']+)',$", text, flags=re.MULTILINE))

    assert assigned == {code: str(tiers.tier_of(code)) for code in tiers.ALL}
    assert bases == {str(tier): str(base) for tier, base in tiers.BASE_FOR_TIER.items()}
    assert assigned["GBR"] == "A"
    assert bases["A"] == "MIDGARD-CORE-GEMMA3-27B-v1.0"


def test_the_console_addresses_a_base_the_way_the_api_expects() -> None:
    """`specification.ts` builds the address itself; it has to be `base_artefact`'s."""
    source = CONSOLE_DEFAULT.read_text(encoding="utf-8")
    template = "`hodd://${site}/models/core/${BASE_FOR_TIER[tierOf(jurisdiction)]}`"
    assert template in source, "the console no longer builds the base address this test mirrors"

    for code in tiers.ALL:
        built = f"hodd://brokkr/models/core/{tiers.BASE_FOR_TIER[tiers.tier_of(code)]}"
        assert built == tiers.base_artefact(code, site="brokkr")


def test_a_table_that_does_not_enumerate_the_programme_is_not_written(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tiers, "TIER_B", tiers.TIER_B[:-1])
    with pytest.raises(tiers.TierError):
        generate_ts_tiers.render()


def test_the_default_the_console_composed_before_this_is_refused() -> None:
    """The regression, from the API's side: GBR against the Tier B base."""
    from draupnir.interfaces.types import RunSpec

    refused = RunSpec.from_mapping(
        {
            "apiVersion": "draupnir/v1",
            "kind": "AdapterRun",
            "metadata": {"name": "cim-gbr-v0.5", "jurisdiction": "GBR", "tier": "A"},
            "spec": {
                "base": {
                    "artefact": f"hodd://sindri/models/core/{tiers.BaseModel.MOE_35B_A3B}",
                    "expectSha256": "a" * 64,
                },
                "dataset": {
                    "artefact": "hodd://corpora/GBR/curated",
                    "expectSha256": "b" * 64,
                    "cutoffPercentile": 99,
                },
                "train": {
                    "driver": "hamarr.llamafactory/v1",
                    "method": "lora",
                    "precision": "bf16",
                    "params": {"rank": 16, "save_steps": 500},
                },
                "placement": {"driver": "motsognir.slurm/v1", "partition": "default", "nodes": 1},
                "evaluate": {
                    "driver": "raun.lmeval/v1",
                    "suites": ["legal-qa"],
                    "gates": ["E1"],
                    "baseline": None,
                },
                "release": {"route": "tier-a", "formats": ["gguf"], "approval": "required"},
            },
        }
    )
    with pytest.raises(config.ConfigurationError):
        config.prepare(refused, site="sindri")
