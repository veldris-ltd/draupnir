"""Lineage: the chain from a released artefact back to licences and corpus hashes.

AC-F11: "The lineage endpoint for any released artefact returns the complete
chain to base model licences and corpus hashes with no gaps."

"With no gaps" is the requirement, and a lineage structure that cannot express
a gap will always satisfy it. So the chain is built as a graph of nodes that
each declare what they were derived from, and `gaps()` walks it looking for
edges whose target is not present. A lineage with a gap is returned *with the
gap named*, and `attest` refuses to produce an attestation from it.

That refusal is what makes AC-F19 and AC-F20 testable from the other end.
Retention deletes a raw corpus after twenty four months and keeps the curated
manifests and licence entries, and the claim is that lineage stays complete
afterwards. It stays complete because a deleted raw corpus is still a node --
its hash and its licence entry survive the deletion -- and if it were not, this
would say so.

SKIDBLADNIR renders the chain; it does not hold the records. The nodes arrive
as mappings from whoever owns them, which is the same seam HODD and GLEIPNIR
use, and is required by the module independence contract in any case.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

SCHEMA = "draupnir/lineage/v1"

#: Node kinds, from the artefact kinds of SAD 7.1 plus the source records that
#: sit behind a corpus.
KINDS: frozenset[str] = frozenset(
    {
        "source",
        "corpus_raw",
        "corpus_curated",
        "base_model",
        "substrate",
        "adapter",
        "merged",
        "quantised",
        "report",
    }
)

#: Kinds that terminate a chain. Reaching one of these is what "complete"
#: means: a base model's licence, or a source's licence and hash.
ROOTS: frozenset[str] = frozenset({"source", "base_model"})


class LineageError(Exception):
    """Raised when a lineage cannot be assembled or attested."""


class IncompleteLineageError(LineageError):
    """Raised when an attestation is asked for over a chain with gaps.

    AC-F11 wants no gaps. Producing an attestation over a broken chain would
    publish a document asserting a provenance nobody can follow, which is worse
    than publishing nothing: it is evidence of something that was not checked.
    """

    def __init__(self, artefact: str, gaps: Sequence[str]) -> None:
        """Name the artefact and every edge that leads nowhere."""
        self.artefact = artefact
        self.gaps = tuple(gaps)
        super().__init__(
            f"the lineage for {artefact[:12]} has {len(self.gaps)} gap(s): "
            f"{', '.join(self.gaps)}. An attestation over a broken chain asserts a "
            "provenance nobody can follow (AC-F11). Attestation is refused."
        )


@dataclass(frozen=True, slots=True)
class Node:
    """One artefact or source in the chain, and what it came from."""

    sha256: str
    kind: str
    #: Hashes this node was derived from. Empty for a root.
    derived_from: tuple[str, ...] = ()
    #: SPDX identifier, for a source or a base model.
    licence: str | None = None
    #: Human-readable name, for the tree view. Never used to resolve a node.
    label: str = ""
    #: Anything else recorded about it: retrieval date, token count, run id.
    facts: Mapping[str, Any] = field(default_factory=dict)
    #: True where the bytes have been deleted under retention but the record
    #: survives. SAD 7.3: the model can afterwards be verified, not re-derived.
    bytes_retained: bool = True

    def __post_init__(self) -> None:
        """Refuse a node that cannot take part in a chain."""
        if self.kind not in KINDS:
            msg = (
                f"{self.kind!r} is not a lineage node kind; expected one of "
                f"{', '.join(sorted(KINDS))}"
            )
            raise LineageError(msg)
        if self.kind in ROOTS and not self.licence:
            msg = (
                f"a {self.kind} node terminates a chain and must carry a licence. "
                "AC-F11 requires the chain to reach base model licences and corpus "
                "hashes; a root with no licence is where it stops short."
            )
            raise LineageError(msg)

    @property
    def is_root(self) -> bool:
        """Whether this node terminates a chain."""
        return self.kind in ROOTS

    def as_payload(self) -> dict[str, Any]:
        """The wire shape, one node of the tree."""
        return {
            "sha256": self.sha256,
            "kind": self.kind,
            "label": self.label,
            "licence": self.licence,
            "derivedFrom": list(self.derived_from),
            "bytesRetained": self.bytes_retained,
            "facts": dict(sorted(self.facts.items())),
        }


@dataclass(frozen=True, slots=True)
class Lineage:
    """Every node reachable from one released artefact."""

    root: str
    nodes: tuple[Node, ...]

    def __post_init__(self) -> None:
        """Refuse a lineage whose subject is not in it."""
        if self.by_hash(self.root) is None:
            msg = f"the lineage for {self.root[:12]} does not contain {self.root[:12]}"
            raise LineageError(msg)

    def by_hash(self, sha256: str) -> Node | None:
        """One node, or `None`."""
        for item in self.nodes:
            if item.sha256 == sha256:
                return item
        return None

    def gaps(self) -> tuple[str, ...]:
        """Every edge whose target is missing, plus every chain that stops short.

        Two kinds of gap, reported together because they mean the same thing to
        a reader: the chain cannot be followed to a licence. An edge pointing
        at nothing is the obvious one. A leaf that is not a root is the subtle
        one -- a curated corpus with no source behind it looks complete until
        somebody asks which licence it was collected under.
        """
        present = {item.sha256 for item in self.nodes}
        found: list[str] = []
        for node in self.nodes:
            for parent in node.derived_from:
                if parent not in present:
                    found.append(f"{node.sha256[:12]} -> {parent[:12]} (missing)")
            if not node.derived_from and not node.is_root:
                found.append(f"{node.sha256[:12]} ({node.kind}) has no origin and is not a root")
        return tuple(sorted(found))

    @property
    def complete(self) -> bool:
        """Whether every chain reaches a licensed root. AC-F11."""
        return not self.gaps()

    def chain_to_roots(self) -> tuple[Node, ...]:
        """Every root reachable from the subject."""
        return tuple(sorted(self.reachable(), key=lambda item: item.sha256))

    def reachable(self) -> tuple[Node, ...]:
        """Every root the subject actually descends from.

        Walked rather than filtered: a node in the collection that nothing
        points at is not part of this artefact's provenance, and including it
        would overstate what the chain shows.
        """
        seen: set[str] = set()
        roots: list[Node] = []
        frontier = [self.root]
        while frontier:
            current = frontier.pop()
            if current in seen:
                continue
            seen.add(current)
            node = self.by_hash(current)
            if node is None:
                continue
            if node.is_root:
                roots.append(node)
            frontier.extend(node.derived_from)
        return tuple(roots)

    def licences(self) -> tuple[str, ...]:
        """Every distinct licence in the chain, sorted."""
        return tuple(sorted({item.licence for item in self.reachable() if item.licence}))

    def corpus_licences(self) -> tuple[str, ...]:
        """Licences of the training content only, excluding the base model.

        The distinction matters at release. The Article 53 training content
        summary describes the data a model was trained on; a base model is a
        component of the model, not content it was trained on, and it is
        documented in the SBOM and the model card instead. Comparing the
        summary against every licence in the chain would report a correct
        summary as stale, every time.
        """
        return tuple(
            sorted(
                {
                    item.licence
                    for item in self.reachable()
                    if item.licence and item.kind == "source"
                }
            )
        )

    def corpus_hashes(self) -> tuple[str, ...]:
        """Every source and corpus hash in the chain. Half of AC-F11."""
        return tuple(
            sorted(
                item.sha256
                for item in self.nodes
                if item.kind in {"source", "corpus_raw", "corpus_curated"}
            )
        )

    def deleted_under_retention(self) -> tuple[Node, ...]:
        """Nodes whose bytes are gone but whose record survives.

        SAD 7.3: after deletion the model "can afterwards be verified but no
        longer re-derived". The distinction belongs on the attestation, because
        a reader checking provenance and a reader planning a rebuild need
        different answers.
        """
        return tuple(item for item in self.nodes if not item.bytes_retained)

    def as_payload(self) -> dict[str, Any]:
        """The tree the lineage endpoint returns."""
        return {
            "schema": SCHEMA,
            "artefact": self.root,
            "complete": self.complete,
            "gaps": list(self.gaps()),
            "licences": list(self.licences()),
            "corpusLicences": list(self.corpus_licences()),
            "corpusHashes": list(self.corpus_hashes()),
            "bytesDeletedUnderRetention": [item.sha256 for item in self.deleted_under_retention()],
            "nodes": [item.as_payload() for item in sorted(self.nodes, key=lambda n: n.sha256)],
        }


@dataclass(frozen=True, slots=True)
class Attestation:
    """A signed statement of a complete lineage."""

    lineage: Lineage
    attested_at: datetime
    #: The approval, rendered by GLEIPNIR. Carries the sole approver exception,
    #: which SAD 9.4 requires to be visible here.
    approval: Mapping[str, Any] = field(default_factory=dict)
    signature: str | None = None

    def canonical(self) -> bytes:
        """The bytes that are signed."""
        return json.dumps(
            {
                "schema": SCHEMA,
                "lineage": self.lineage.as_payload(),
                "attestedAt": self.attested_at.isoformat(),
                "approval": dict(sorted(self.approval.items())),
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")

    def digest(self) -> str:
        """SHA-256 of the attestation, for the release manifest."""
        return hashlib.sha256(self.canonical()).hexdigest()

    def as_payload(self) -> dict[str, Any]:
        """The published shape."""
        return {
            "schema": SCHEMA,
            "attestedAt": self.attested_at.isoformat(),
            "digest": self.digest(),
            "signature": self.signature,
            "approval": dict(sorted(self.approval.items())),
            "lineage": self.lineage.as_payload(),
        }

    def to_json(self) -> str:
        """The attestation as a release artefact."""
        return json.dumps(self.as_payload(), indent=2, sort_keys=True, ensure_ascii=False)


def build(root: str, nodes: Iterable[Node]) -> Lineage:
    """Assemble a lineage from recorded nodes."""
    return Lineage(root=root, nodes=tuple(nodes))


def from_mappings(root: str, records: Iterable[Mapping[str, Any]]) -> Lineage:
    """Assemble a lineage from records rendered by whoever holds them.

    The seam. HODD holds the sources and the corpora, the ledger holds the
    runs, and SKIDBLADNIR imports neither: it receives mappings, exactly as a
    policy driver receives licence facts.
    """
    return build(
        root,
        (
            Node(
                sha256=str(item["sha256"]),
                kind=str(item["kind"]),
                derived_from=tuple(item.get("derivedFrom", ())),
                licence=item.get("licence"),
                label=str(item.get("label", "")),
                facts=dict(item.get("facts", {})),
                bytes_retained=bool(item.get("bytesRetained", True)),
            )
            for item in records
        ),
    )


def attest(
    lineage: Lineage,
    *,
    attested_at: datetime,
    approval: Mapping[str, Any] | None = None,
    signature: str | None = None,
) -> Attestation:
    """Produce an attestation, or refuse because the chain has gaps. AC-F11."""
    if not lineage.complete:
        raise IncompleteLineageError(lineage.root, lineage.gaps())
    return Attestation(
        lineage=lineage,
        attested_at=attested_at,
        approval=dict(approval or {}),
        signature=signature,
    )
