"""The CycloneDX SBOM for a released model.

AC-F10 asks a release to produce "a model card, a CycloneDX SBOM, a SHA-256
manifest and a lineage attestation, all four present and internally
consistent". This is the second of the four, and "internally consistent" is the
part that takes the design work: the SBOM, the card, the manifest and the
attestation must agree about which artefact this is, and the way they stop
agreeing is each being assembled from its own copy of the same facts.

So the SBOM is built from the same inputs the rest of the package is built
from -- the artefact hash, the lineage nodes -- and the consistency check in
`release.py` compares them rather than trusting that they were built correctly.

A model SBOM is not a software SBOM with different words in it. The components
that matter are the base model, the corpora and their licences, because those
are what a customer's counsel will ask about. Python packages are recorded for
the pipeline that produced the model, not as components of the model itself,
and the distinction is kept visible in the component types.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

SPEC_VERSION = "1.6"
SCHEMA = "https://cyclonedx.org/schema/bom-1.6.schema.json"

#: CycloneDX component types. `machine-learning-model` and `data` are what make
#: this a model SBOM rather than a dependency list.
MODEL = "machine-learning-model"
DATA = "data"
LIBRARY = "library"


class SbomError(Exception):
    """Raised when an SBOM cannot be assembled."""


@dataclass(frozen=True, slots=True)
class Component:
    """One component of the released model."""

    name: str
    type: str
    sha256: str
    version: str = ""
    licence: str | None = None
    #: Where it came from, for a reader following provenance.
    description: str = ""

    def bom_ref(self) -> str:
        """A stable reference, from the hash rather than from the name.

        Two corpora with the same name and different content are two
        components, and a name-based reference would collapse them.
        """
        return f"{self.type}:{self.sha256[:16]}"

    def as_payload(self) -> dict[str, Any]:
        """The CycloneDX component shape."""
        payload: dict[str, Any] = {
            "bom-ref": self.bom_ref(),
            "type": self.type,
            "name": self.name,
            "hashes": [{"alg": "SHA-256", "content": self.sha256}],
        }
        if self.version:
            payload["version"] = self.version
        if self.licence:
            payload["licenses"] = [{"license": {"id": self.licence}}]
        if self.description:
            payload["description"] = self.description
        return payload


@dataclass(frozen=True, slots=True)
class Sbom:
    """A CycloneDX bill of materials for one released model."""

    subject: Component
    components: tuple[Component, ...]
    generated_at: datetime
    #: The tool that produced the model, recorded as the pipeline rather than
    #: as a component of the model.
    tools: tuple[Mapping[str, str], ...] = field(default_factory=tuple)
    serial_number: str = ""

    def __post_init__(self) -> None:
        """Refuse an SBOM that does not describe the artefact it claims to."""
        if self.subject.type != MODEL:
            msg = (
                f"the subject of a model SBOM is a {MODEL}, not a {self.subject.type!r}. "
                "An SBOM whose subject is a component describes the wrong thing."
            )
            raise SbomError(msg)

    @property
    def licences(self) -> tuple[str, ...]:
        """Every distinct licence across the components."""
        return tuple(sorted({item.licence for item in self.components if item.licence}))

    def as_payload(self) -> dict[str, Any]:
        """The CycloneDX document."""
        return {
            "$schema": SCHEMA,
            "bomFormat": "CycloneDX",
            "specVersion": SPEC_VERSION,
            "serialNumber": self.serial_number or f"urn:uuid:{self.subject.sha256[:32]}",
            "version": 1,
            "metadata": {
                "timestamp": self.generated_at.isoformat(),
                "component": self.subject.as_payload(),
                "tools": {"components": [dict(item) for item in self.tools]},
            },
            "components": [
                item.as_payload()
                for item in sorted(self.components, key=lambda c: (c.type, c.name, c.sha256))
            ],
        }

    def canonical(self) -> bytes:
        """Deterministic bytes, for the release manifest's hash."""
        return json.dumps(
            self.as_payload(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")

    def digest(self) -> str:
        """SHA-256 of the SBOM."""
        return hashlib.sha256(self.canonical()).hexdigest()

    def to_json(self) -> str:
        """The SBOM as a release artefact."""
        return json.dumps(self.as_payload(), indent=2, sort_keys=True, ensure_ascii=False)


def from_lineage(
    *,
    model: str,
    version: str,
    artefact_sha256: str,
    lineage_nodes: Iterable[Mapping[str, Any]],
    generated_at: datetime,
    tools: Sequence[Mapping[str, str]] = (),
) -> Sbom:
    """Build the SBOM from the same lineage the attestation is built from.

    One source for both, so "internally consistent" is a property of how the
    package is assembled rather than something the consistency check has to
    discover after the fact.
    """
    components: list[Component] = []
    for node in lineage_nodes:
        sha256 = str(node["sha256"])
        if sha256 == artefact_sha256:
            continue
        kind = str(node["kind"])
        components.append(
            Component(
                name=str(node.get("label") or f"{kind}:{sha256[:12]}"),
                type=_component_type(kind),
                sha256=sha256,
                licence=node.get("licence"),
                description=_describe(kind, node),
            )
        )

    return Sbom(
        subject=Component(name=model, type=MODEL, sha256=artefact_sha256, version=version),
        components=tuple(components),
        generated_at=generated_at,
        tools=tuple(dict(item) for item in tools),
    )


def _component_type(kind: str) -> str:
    """The CycloneDX type for a lineage node kind."""
    if kind in {"base_model", "substrate", "adapter", "merged", "quantised"}:
        return MODEL
    if kind in {"source", "corpus_raw", "corpus_curated"}:
        return DATA
    return LIBRARY


def _describe(kind: str, node: Mapping[str, Any]) -> str:
    """A one-line description, including whether the bytes still exist.

    A corpus deleted under retention stays in the SBOM. Its hash and licence
    are what the document is for, and removing the row would make a release
    look as though it were trained on less than it was.
    """
    if not node.get("bytesRetained", True):
        return (
            f"{kind}; bytes deleted under the retention policy, record retained so "
            "the model can be verified but not re-derived (SAD 7.3)"
        )
    return kind
