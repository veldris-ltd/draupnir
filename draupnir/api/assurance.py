"""Where GLEIPNIR's gate definitions meet RAUN's execution.

The modules are independent siblings and cannot import each other, which is
Decision S4 held as an import contract rather than as a convention. So the
wiring lives here, in the edge layer, which sits above every module and is the
only place permitted to know two of them at once.

It is four lines of adaptation and it is worth its own module, because it is
the seam an architecture review should be able to find. Anything that grows
here beyond translation -- a threshold, a fallback, a "just this once" -- is
policy that has escaped GLEIPNIR, and it will be visible in this file rather
than dispersed through the call sites.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from draupnir.gleipnir import gates
from draupnir.gleipnir.gates import SuiteResult
from draupnir.raun.judging import Judge


def gleipnir_judge(
    measurements: Mapping[str, float],
    baselines: Mapping[str, float],
    *,
    suite_version: str,
    gate_ids: Sequence[str],
) -> SuiteResult:
    """Judge measurements against GLEIPNIR's definitions of the named gates.

    Satisfies `raun.judging.Judge`. The gate identifiers arrive from the suite
    and are resolved here, so RAUN never holds a threshold and there is exactly
    one place a margin is written down.
    """
    return gates.evaluate(
        measurements,
        baselines,
        suite_version=suite_version,
        gates=[gates.get(gate_id) for gate_id in gate_ids],
    )


#: Named so a reader can see the protocol is satisfied without running mypy.
JUDGE: Judge = gleipnir_judge


def gate_registry() -> tuple[dict[str, object], ...]:
    """Every defined gate, for the console and the release package.

    Passes through rather than re-deriving: a second rendering of the gate
    table is a second answer to what the thresholds are.
    """
    return gates.registry()
