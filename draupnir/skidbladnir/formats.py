"""Export formats, and the cross-platform quantisation check.

Three formats reach a customer: NVFP4 for the NVIDIA appliances, GGUF for
llama.cpp, MLX for Apple silicon. The first two are built on the forge; MLX is
built on ALVISS, which is the only Apple silicon in the estate.

The requirement that shapes this module:

    MLX export runs on ALVISS and its results are compared against the NVFP4
    results as a cross-platform quantisation check. A divergence beyond
    threshold raises rather than passing.

The reason it is worth doing is that a quantisation bug does not look like a
bug. Both builds load, both generate fluent text, and both score close enough
to the dense model to clear gates that were written to tolerate quantisation
loss. What catches it is the two builds disagreeing with each other: they came
from the same weights, so a divergence between them is not model quality, it is
one of the two pipelines being wrong.

`DIVERGENCE_THRESHOLD` is therefore tighter than any gate margin. A gate asks
whether the model is good enough; this asks whether two builds of one model are
the same model, and the answer should be yes to well within the noise a gate
tolerates.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class Format(StrEnum):
    """The quantisation and packaging formats of SAD 6.2."""

    NVFP4 = "nvfp4"
    GGUF_Q4KM = "gguf-q4km"
    MLX4 = "mlx4"


#: Which appliance family each format is built on. MLX cannot be produced on
#: the forge at all, which is why the check exists to be run across two hosts.
BUILD_HOST: dict[Format, str] = {
    Format.NVFP4: "sindri",
    Format.GGUF_Q4KM: "sindri",
    Format.MLX4: "alviss",
}

#: The reference build every other build is compared against. NVFP4 because it
#: is the one produced on the same hardware the model was trained on, so a
#: divergence is most likely to be in the other build.
REFERENCE = Format.NVFP4

#: How far two builds of the same weights may differ before the difference is
#: a defect rather than quantisation noise. Deliberately tighter than the
#: tightest gate margin (E5 at 0.01): a gate asks whether the model is good
#: enough, this asks whether two builds are the same model.
DIVERGENCE_THRESHOLD = 0.005


class FormatError(Exception):
    """Raised when a format cannot be built or checked."""


class DivergenceError(FormatError):
    """Raised when two builds of one model do not agree.

    Raises rather than failing a gate, because this is not a statement about
    the model. Both builds may clear every gate and still be different models,
    and a gate failure would send somebody to look at training data for a
    problem that is in a conversion script.
    """

    def __init__(
        self,
        reference: str,
        candidate: str,
        divergences: Sequence[tuple[str, float]],
        threshold: float,
    ) -> None:
        """Name the two builds and every measurement they disagree on."""
        self.reference = reference
        self.candidate = candidate
        self.divergences = tuple(divergences)
        self.threshold = threshold
        detail = "; ".join(f"{gate} differs by {delta:.4f}" for gate, delta in divergences)
        super().__init__(
            f"the {candidate} build diverges from the {reference} build beyond "
            f"{threshold}: {detail}. These are two builds of the same weights, so a "
            "difference between them is not model quality -- it is one of the two "
            "conversion pipelines being wrong. Both may still pass their gates, "
            "which is why this raises rather than failing one."
        )


@dataclass(frozen=True, slots=True)
class Divergence:
    """One measurement, on two builds."""

    gate: str
    reference_value: float
    candidate_value: float

    @property
    def delta(self) -> float:
        """How far apart they are, unsigned."""
        return round(abs(self.candidate_value - self.reference_value), 6)

    def exceeds(self, threshold: float) -> bool:
        """Whether the two builds disagree beyond tolerance."""
        return self.delta > threshold

    def as_payload(self) -> dict[str, Any]:
        """The wire shape."""
        return {
            "gate": self.gate,
            "reference": self.reference_value,
            "candidate": self.candidate_value,
            "delta": self.delta,
        }


@dataclass(frozen=True, slots=True)
class CrossCheck:
    """The comparison of one build against the reference build."""

    reference_format: str
    candidate_format: str
    reference_sha256: str
    candidate_sha256: str
    divergences: tuple[Divergence, ...]
    threshold: float = DIVERGENCE_THRESHOLD
    #: Where each was built. Recorded because the check is cross-platform and
    #: "both were built on Sindri" would mean it did not test what it claims to.
    reference_host: str = ""
    candidate_host: str = ""

    @property
    def exceeded(self) -> tuple[Divergence, ...]:
        """Measurements that disagree beyond tolerance."""
        return tuple(item for item in self.divergences if item.exceeds(self.threshold))

    @property
    def agrees(self) -> bool:
        """Whether the two builds are the same model within tolerance."""
        return not self.exceeded

    @property
    def cross_platform(self) -> bool:
        """Whether the two builds actually came from different hosts."""
        return bool(self.reference_host) and self.reference_host != self.candidate_host

    def raise_for_divergence(self) -> None:
        """Raise if the builds disagree. The requirement's "raises rather than passing"."""
        if self.exceeded:
            raise DivergenceError(
                self.reference_format,
                self.candidate_format,
                [(item.gate, item.delta) for item in self.exceeded],
                self.threshold,
            )

    def as_payload(self) -> dict[str, Any]:
        """The release-package shape."""
        return {
            "referenceFormat": self.reference_format,
            "candidateFormat": self.candidate_format,
            "referenceSha256": self.reference_sha256,
            "candidateSha256": self.candidate_sha256,
            "referenceHost": self.reference_host or None,
            "candidateHost": self.candidate_host or None,
            "crossPlatform": self.cross_platform,
            "threshold": self.threshold,
            "agrees": self.agrees,
            "divergences": [item.as_payload() for item in self.divergences],
        }


def cross_check(
    *,
    reference_format: str,
    candidate_format: str,
    reference_sha256: str,
    candidate_sha256: str,
    reference_measurements: Mapping[str, float],
    candidate_measurements: Mapping[str, float],
    threshold: float = DIVERGENCE_THRESHOLD,
) -> CrossCheck:
    """Compare two builds of one model, gate by gate.

    A measurement present on one build and not the other is refused rather than
    skipped. Comparing on the intersection would quietly narrow the check every
    time a build failed to report something, and the narrowing would be
    invisible -- the check would still say it agreed.
    """
    missing = sorted(set(reference_measurements) ^ set(candidate_measurements))
    if missing:
        msg = (
            f"the {reference_format} and {candidate_format} builds were measured on "
            f"different things: {', '.join(missing)} is present on one and not the "
            "other. Comparing on what they share would narrow the check silently."
        )
        raise FormatError(msg)

    return CrossCheck(
        reference_format=reference_format,
        candidate_format=candidate_format,
        reference_sha256=reference_sha256,
        candidate_sha256=candidate_sha256,
        divergences=tuple(
            Divergence(
                gate=gate,
                reference_value=reference_measurements[gate],
                candidate_value=candidate_measurements[gate],
            )
            for gate in sorted(reference_measurements)
        ),
        threshold=threshold,
        reference_host=BUILD_HOST.get(Format(reference_format), ""),
        candidate_host=BUILD_HOST.get(Format(candidate_format), ""),
    )


def check_mlx_against_nvfp4(
    *,
    nvfp4_sha256: str,
    nvfp4_measurements: Mapping[str, float],
    mlx_sha256: str,
    mlx_measurements: Mapping[str, float],
    threshold: float = DIVERGENCE_THRESHOLD,
) -> CrossCheck:
    """The cross-platform quantisation check, and it raises on divergence.

    Named for what it is rather than left as a general comparison, because the
    requirement names it: MLX is built on ALVISS and NVFP4 on the forge, and
    the point of comparing them is that the hosts differ.
    """
    result = cross_check(
        reference_format=str(REFERENCE),
        candidate_format=str(Format.MLX4),
        reference_sha256=nvfp4_sha256,
        candidate_sha256=mlx_sha256,
        reference_measurements=nvfp4_measurements,
        candidate_measurements=mlx_measurements,
        threshold=threshold,
    )
    result.raise_for_divergence()
    return result
