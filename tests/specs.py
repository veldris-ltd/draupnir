"""Specifications a test can submit. RF-11.

`interfaces.testing.sample_spec` is SAD 6.2's worked example, transcribed. It
is *internally inconsistent*: it declares GBR as Tier A and points at
`MIDGARD-CORE-QWEN36-35B-A3B-v1.0`, which SAD section 13.5 assigns to Tier B.
`draupnir/hamarr/config.py` records the resolution -- the rule is authoritative
and the example is stale -- and since RF-11 the submission path enforces it, so
the example as written is refused with the mismatch named.

That refusal is correct and there is a test for it. This module is for the
tests that need a specification the estate will *accept*, which is most of
them: they are about idempotency, identity, the ledger and the stream, and they
should fail when those break rather than when the worked example is corrected.
"""

from __future__ import annotations

from typing import Any

from draupnir.hamarr import config, tiers
from draupnir.interfaces.testing import sample_spec
from draupnir.interfaces.testing.fixtures import SAMPLE_SPEC_MAPPING
from draupnir.interfaces.types import RunSpec

#: The jurisdiction the worked example is for.
JURISDICTION = str(SAMPLE_SPEC_MAPPING["metadata"]["jurisdiction"])


def submittable_spec(**overrides: Any) -> RunSpec:
    """The worked example with the base its tier requires, as a client writes it.

    No `save_steps`: an operator does not author one, and HAMARR derives it
    from the run's time budget at submission. A training driver refuses to
    invent one -- correctly, since a wrong checkpoint interval costs a run its
    resumability -- which is why `admit` prepares before it validates.

    Overrides are shallow-merged into the `spec` block, as `sample_spec` does,
    and are applied after the correction so a test can still ask for a
    specification that is wrong in a particular way.
    """
    return sample_spec(
        base={
            "artefact": tiers.base_artefact(JURISDICTION),
            "expectSha256": SAMPLE_SPEC_MAPPING["spec"]["base"]["expectSha256"],
        },
        **overrides,
    )


def submittable_mapping(**overrides: Any) -> dict[str, Any]:
    """The same, in the wire shape a client posts."""
    return submittable_spec(**overrides).as_mapping()


def prepared_spec(**overrides: Any) -> RunSpec:
    """What the submission path settles that specification into.

    Through `config.prepare`, which is what `admit` calls: the tier checked,
    the base checked against it, the checkpoint interval derived. This is the
    form the chain records and the worker renders, so a test standing in for
    "what the system would have written down" wants this one.
    """
    settled, _policy = config.prepare(submittable_spec(**overrides))
    return settled
