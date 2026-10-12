"""GULLINBURSTI's link to MEGINGJORD, without a network. G8.

`gullinbursti.protocol.Link` is the half of VLD-SAD-MEGINGJORD-001 section 33
this repository owns. These tests drive it against a fake HTTP client that
answers as MEGINGJORD does, with receipts signed by a real anchor key, so what
is checked is the protocol and not a stand-in for it: the documents sent are
the shared package's, signed with the bundle's key, and a receipt is believed
only once it verifies offline under the published anchor key.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)
from shared_protocol import (
    AnchorReceipt,
    Ed25519Signer,
    Envelope,
    KeyPurpose,
    Keyring,
    MLDSA65Signer,
    ModelRelease,
    PublishedKey,
    ReceiptSite,
    parse_digest,
    seal,
    verify,
)

from draupnir.core.domain.federation import AnchorOutcome, AnchorSubmission, ContentLeakError
from draupnir.core.domain.ledger import ChainHead
from draupnir.gullinbursti import protocol

SITE = uuid.UUID("06aca525-0e73-74a8-8000-421f4b681171")
ANCHOR_KEY = "meg-anchor-2026-10"
SITE_KEY = "site-sindri-2026-10"
WHEN = datetime(2026, 10, 12, 9, 30, tzinfo=UTC)


# -- the bundle, as MEGINGJORD's installer and enrol.sh leave it ---------------


def anchor_signers(key_id: str = ANCHOR_KEY) -> list[Ed25519Signer | MLDSA65Signer]:
    """MEGINGJORD's anchor key: both algorithms, as its receipts carry (AC-R2)."""
    return [
        Ed25519Signer.from_seed(key_id, b"\x01" * 32),
        MLDSA65Signer.from_seed(key_id, b"\x02" * 32),
    ]


def write_bundle(folder: Path, site_key: Ed25519PrivateKey) -> Path:
    """A complete bundle in `folder`."""
    (folder / "keys").mkdir(parents=True)
    (folder / "megingjord.toml").write_text(
        f"""site_name = "sindri"
site_id = "{SITE}"
ordinal = 0
endpoint = "https://megingjord.veldris.internal"
endpoint_address = "10.90.0.1"
server_ca = "console-ca.crt"
federation_root = "root.crt"
wireguard = "wg0.conf"
anchor_key = "keys/{ANCHOR_KEY}.json"
policy_key = "keys/meg-policy-2026-10.json"
checkpoint_key = "keys/meg-ckpt-2026-10.json"
client_key = "site-mtls.key"
client_certificate = "site.crt"
signing_key = "site-signing.key"
"""
    )
    for name in ("console-ca.crt", "root.crt", "site.crt", "site-mtls.key"):
        (folder / name).write_text("-----BEGIN PLACEHOLDER-----\n")
    (folder / "site-signing.key").write_bytes(
        site_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    )
    (folder / "enrol-request.json").write_text(
        json.dumps({"csr": "...", "signing_key": {"key_id": SITE_KEY}})
    )
    published = PublishedKey(
        ANCHOR_KEY, KeyPurpose.ANCHOR, tuple(s.public_key() for s in anchor_signers())
    )
    (folder / "keys" / f"{ANCHOR_KEY}.json").write_text(json.dumps(published.to_wire()))
    return folder


class Answer:
    """One HTTP response."""

    def __init__(self, status_code: int, body: Any) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> Any:
        return self._body


class FakeMegingjord:
    """Answers each request with what `respond` returns, and remembers it."""

    def __init__(self, respond: Callable[[str, str, Any], Answer]) -> None:
        self.respond = respond
        self.requests: list[tuple[str, str, Mapping[str, Any] | None, Mapping[str, str]]] = []

    def get(self, url: str, *, params: Mapping[str, str] | None = None) -> Answer:
        del params
        self.requests.append(("GET", url, None, {}))
        return self.respond("GET", url, None)

    def post(
        self,
        url: str,
        *,
        json: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Answer:
        self.requests.append(("POST", url, json, dict(headers or {})))
        return self.respond("POST", url, json)


def receipt_for(document: Mapping[str, Any], **changes: Any) -> dict[str, Any]:
    """MEGINGJORD's signed receipt for a submitted head, as it answers 201."""
    fields: dict[str, Any] = {
        "site": ReceiptSite(SITE, "sindri", 0),
        "seq": document["seq"],
        "entry_hash": parse_digest(document["entry_hash"]),
        "anchored_at": WHEN,
        "checkpoint_seq": 0,
        "key_id": ANCHOR_KEY,
        "algorithms": ("ed25519", "ml-dsa-65"),
    }
    fields.update(changes)
    signers = anchor_signers(fields["key_id"])
    receipt = AnchorReceipt(**fields)
    return {"document": receipt.to_wire(), "envelope": seal(receipt, signers).to_wire()}


def problem(slug: str, status: int, detail: str) -> Answer:
    return Answer(
        status,
        {
            "type": f"https://veldris.internal/problems/megingjord/{slug}",
            "title": slug,
            "status": status,
            "detail": detail,
        },
    )


@pytest.fixture
def site_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


@pytest.fixture
def bundle(tmp_path: Path, site_key: Ed25519PrivateKey) -> protocol.Bundle:
    return protocol.Bundle.load(write_bundle(tmp_path / "megingjord", site_key))


def link(bundle: protocol.Bundle, fake: FakeMegingjord) -> protocol.Link:
    return protocol.Link.from_bundle(bundle, fake)


def head(seq: int = 12, previous: str | None = "b" * 64) -> AnchorSubmission:
    return AnchorSubmission(
        head=ChainHead(site_id="sindri", seq=seq, entry_hash=f"{seq:064x}"),
        previous_hash=previous,
        submitted_at=WHEN,
    )


# -- the bundle --------------------------------------------------------------


def test_the_bundle_is_read_as_megingjord_wrote_it(bundle: protocol.Bundle) -> None:
    assert bundle.site_id == SITE
    assert bundle.site_name == "sindri"
    assert bundle.endpoint == "https://megingjord.veldris.internal"
    # MEGINGJORD's name for the key, from the request enrol.sh wrote: not one
    # derived here, which MEGINGJORD would never have heard of.
    assert bundle.key_id == SITE_KEY
    assert bundle.anchor_key.name == f"{ANCHOR_KEY}.json"


def test_a_folder_that_is_not_a_bundle_says_what_to_copy(tmp_path: Path) -> None:
    with pytest.raises(protocol.BundleError, match=r"enrol\.sh"):
        protocol.Bundle.load(tmp_path)


def test_a_bundle_before_enrolment_names_what_is_missing(
    tmp_path: Path, site_key: Ed25519PrivateKey
) -> None:
    folder = write_bundle(tmp_path / "megingjord", site_key)
    (folder / "site-signing.key").unlink()
    (folder / "site.crt").unlink()
    with pytest.raises(protocol.BundleError) as raised:
        protocol.Bundle.load(folder)
    assert "site-signing.key" in str(raised.value)
    assert "site.crt" in str(raised.value)
    assert "enrol.sh" in str(raised.value)


# -- anchoring ---------------------------------------------------------------


def test_a_head_is_sent_as_the_shared_protocol_signs_it(
    bundle: protocol.Bundle, site_key: Ed25519PrivateKey
) -> None:
    """SAD 12.1: the document, the site's envelope, at MEGINGJORD's address."""
    fake = FakeMegingjord(lambda method, url, body: Answer(201, receipt_for(body["document"])))

    link(bundle, fake).countersign(head(), at=WHEN, countersignature="")

    method, url, body, headers = fake.requests[0]
    assert (method, url) == (
        "POST",
        f"https://megingjord.veldris.internal/v1/sites/{SITE}/anchors",
    )
    assert body is not None
    document = body["document"]
    assert document["site_id"] == str(SITE)
    assert document["seq"] == 12
    assert document["entry_hash"] == f"sha256:{12:064x}"
    assert document["prev_anchor_hash"] == f"sha256:{'b' * 64}"
    assert document["agent"] == protocol.AGENT
    assert protocol.AGENT.startswith("gullinbursti/") and "shared-protocol/" in protocol.AGENT
    # Signed by this forge's key, under the identifier MEGINGJORD registered.
    published = PublishedKey(
        SITE_KEY,
        KeyPurpose.SITE,
        (Ed25519Signer(SITE_KEY, site_key).public_key(),),
    )
    verify(
        document,
        Envelope.from_wire(body["envelope"]),
        Keyring([published]),
        purpose=KeyPurpose.SITE,
        key_id=SITE_KEY,
    )
    # The same head is the same key, so a retry is a replay and never a
    # second anchor.
    again = FakeMegingjord(fake.respond)
    link(bundle, again).countersign(head(), at=WHEN, countersignature="")
    assert headers["Idempotency-Key"] == again.requests[0][3]["Idempotency-Key"]


def test_the_first_anchor_names_no_predecessor(bundle: protocol.Bundle) -> None:
    fake = FakeMegingjord(lambda method, url, body: Answer(201, receipt_for(body["document"])))
    link(bundle, fake).countersign(head(previous=None), at=WHEN, countersignature="")
    assert fake.requests[0][2]["document"]["prev_anchor_hash"] is None  # type: ignore[index]


def test_a_countersigned_head_carries_the_verified_receipt(bundle: protocol.Bundle) -> None:
    fake = FakeMegingjord(lambda method, url, body: Answer(201, receipt_for(body["document"])))

    receipt = link(bundle, fake).countersign(head(), at=WHEN, countersignature="")

    assert receipt.outcome is AnchorOutcome.COUNTERSIGNED
    assert receipt.anchor is not None
    assert receipt.anchor.countersigned_at == WHEN
    # The signed receipt itself, as a purchaser would verify it offline.
    kept = json.loads(receipt.anchor.countersignature)
    assert kept["document"]["seq"] == 12
    assert len(kept["envelope"]["signatures"]) == 2


def test_an_identical_resubmission_is_a_duplicate(bundle: protocol.Bundle) -> None:
    fake = FakeMegingjord(lambda method, url, body: Answer(200, receipt_for(body["document"])))
    receipt = link(bundle, fake).countersign(head(), at=WHEN, countersignature="")
    assert receipt.outcome is AnchorOutcome.DUPLICATE
    assert receipt.accepted


def test_a_receipt_that_does_not_verify_is_not_an_anchor(bundle: protocol.Bundle) -> None:
    """Signed by a key MEGINGJORD did not publish: refused, however it answered."""
    fake = FakeMegingjord(
        lambda method, url, body: Answer(
            201, receipt_for(body["document"], key_id="meg-anchor-2099-01")
        )
    )
    receipt = link(bundle, fake).countersign(head(), at=WHEN, countersignature="")
    assert not receipt.accepted
    assert receipt.problem == "receipt-invalid"
    assert "does not verify" in receipt.reason


def test_receipts_under_a_rotated_anchor_key_verify_through_the_changeover(
    bundle: protocol.Bundle,
) -> None:
    """MEGINGJORD's yearly rotation (its SAD 14.2, 31.4) does not interrupt anchoring.

    The next key is published into the bundle before MEGINGJORD signs with it;
    a receipt under it verifies, and so does one under the key still named.
    """
    next_key = "meg-anchor-2027-10"
    published = PublishedKey(
        next_key, KeyPurpose.ANCHOR, tuple(s.public_key() for s in anchor_signers(next_key))
    )
    (bundle.folder / "keys" / f"{next_key}.json").write_text(json.dumps(published.to_wire()))
    rotated = protocol.Link.from_bundle(
        bundle,
        FakeMegingjord(
            lambda method, url, body: Answer(201, receipt_for(body["document"], key_id=next_key))
        ),
    )
    assert rotated.countersign(head(), at=WHEN, countersignature="").accepted
    current = protocol.Link.from_bundle(
        bundle, FakeMegingjord(lambda method, url, body: Answer(201, receipt_for(body["document"])))
    )
    assert current.countersign(head(), at=WHEN, countersignature="").accepted


def test_a_receipt_for_another_head_is_not_this_heads(bundle: protocol.Bundle) -> None:
    fake = FakeMegingjord(
        lambda method, url, body: Answer(201, receipt_for(body["document"], seq=11))
    )
    receipt = link(bundle, fake).countersign(head(), at=WHEN, countersignature="")
    assert not receipt.accepted
    assert receipt.problem == "receipt-invalid"


def test_a_head_accepted_and_not_yet_countersigned_waits(bundle: protocol.Bundle) -> None:
    """MEGINGJORD's 202: its signing key is away. Queued there, and here."""
    fake = FakeMegingjord(
        lambda method, url, body: Answer(202, {"anchor_id": str(SITE), "seq": 12})
    )
    receipt = link(bundle, fake).countersign(head(), at=WHEN, countersignature="")
    assert not receipt.accepted
    assert receipt.problem == protocol.PENDING
    assert "countersigned in order when the key returns" in receipt.reason


def test_a_fork_is_a_divergence_in_megingjords_words(bundle: protocol.Bundle) -> None:
    fake = FakeMegingjord(
        lambda method, url, body: problem(
            "fork-detected", 409, "seq 12 already anchored with another entry_hash"
        )
    )
    receipt = link(bundle, fake).countersign(head(), at=WHEN, countersignature="")
    assert receipt.outcome is AnchorOutcome.DIVERGED
    assert receipt.problem == protocol.FORK_DETECTED
    assert "already anchored with another entry_hash" in receipt.reason


def test_a_quarantined_site_is_refused_with_the_reason(bundle: protocol.Bundle) -> None:
    fake = FakeMegingjord(
        lambda method, url, body: problem(
            "site-not-anchorable", 409, "the site is QUARANTINED and cannot anchor"
        )
    )
    receipt = link(bundle, fake).countersign(head(), at=WHEN, countersignature="")
    assert receipt.outcome is AnchorOutcome.REJECTED
    assert receipt.problem == protocol.NOT_ANCHORABLE
    assert "QUARANTINED" in receipt.reason


def test_an_unreachable_megingjord_is_a_receipt_not_an_exception(
    bundle: protocol.Bundle,
) -> None:
    """A partition is a degraded mode, not a fault that reaches a run (S8)."""

    def refuse(method: str, url: str, body: Any) -> Answer:
        raise OSError("connection refused")

    receipt = link(bundle, FakeMegingjord(refuse)).countersign(head(), at=WHEN, countersignature="")
    assert not receipt.accepted
    assert receipt.problem == protocol.UNREACHABLE
    assert "could not be reached" in receipt.reason


def test_megingjords_head_is_read_in_this_forges_hash_form(bundle: protocol.Bundle) -> None:
    fake = FakeMegingjord(
        lambda method, url, body: Answer(
            200,
            {
                "site_id": str(SITE),
                "state": "ACTIVE",
                "head_seq": 41,
                "head_hash": f"sha256:{'c' * 64}",
                "last_anchored_at": None,
            },
        )
    )
    found = link(bundle, fake).head()
    assert found == protocol.RemoteHead(state="ACTIVE", seq=41, entry_hash="c" * 64)
    assert fake.requests[0][:2] == (
        "GET",
        f"https://megingjord.veldris.internal/v1/sites/{SITE}/head",
    )


def test_a_site_megingjord_has_never_heard_from_has_no_head(bundle: protocol.Bundle) -> None:
    fake = FakeMegingjord(
        lambda method, url, body: Answer(
            200,
            {"site_id": str(SITE), "state": "ENROLLING", "head_seq": None, "head_hash": None},
        )
    )
    assert link(bundle, fake).head() == protocol.RemoteHead(
        state="ENROLLING", seq=None, entry_hash=None
    )


# -- releases ----------------------------------------------------------------


def release(**changes: Any) -> ModelRelease:
    fields: dict[str, Any] = {
        "site_id": SITE,
        "name": "cim-gbr",
        "version": "1.0",
        "jurisdiction": "GBR",
        "tier": "tier-a",
        "manifest_sha256": parse_digest(f"sha256:{'d' * 64}"),
        "anchor_seq": 12,
        "card_uri": "https://alviss.sindri.veldris.internal/v1/releases/d/documents/model-card",
        "sbom_uri": "https://alviss.sindri.veldris.internal/v1/releases/d/documents/sbom",
        "lineage_uri": "https://alviss.sindri.veldris.internal/v1/releases/d/documents/lineage",
        "training_summary_uri": (
            "https://alviss.sindri.veldris.internal/v1/releases/d/documents/training-summary"
        ),
        "released_at": WHEN,
    }
    fields.update(changes)
    return ModelRelease(**fields)


def test_a_registered_release_brings_back_the_estate_posture(bundle: protocol.Bundle) -> None:
    posture = {"v": 1, "recorded": True, "profile": "Guided."}
    fake = FakeMegingjord(
        lambda method, url, body: Answer(
            201,
            {
                "model_id": "m-1",
                "receipt_id": "r-1",
                "replay": False,
                "estate_posture": posture,
            },
        )
    )
    answer = link(bundle, fake).register(release())
    assert answer == protocol.Registration(
        model_id="m-1", receipt_id="r-1", replay=False, estate_posture=posture
    )
    _, url, body, headers = fake.requests[0]
    assert url == "https://megingjord.veldris.internal/v1/registry/models"
    assert body is not None and body["document"]["anchor_seq"] == 12
    assert headers["Idempotency-Key"]


def test_a_refused_registration_keeps_megingjords_reason(bundle: protocol.Bundle) -> None:
    fake = FakeMegingjord(
        lambda method, url, body: problem(
            "site-cannot-release",
            409,
            "the site is QUARANTINED: it cannot obtain a receipt, so it cannot release",
        )
    )
    answer = link(bundle, fake).register(release())
    assert isinstance(answer, protocol.Refusal)
    assert answer.problem == protocol.SITE_CANNOT_RELEASE
    assert "QUARANTINED" in answer.reason


def test_a_record_carrying_content_is_refused_before_it_is_sent(
    bundle: protocol.Bundle,
) -> None:
    """AC-S14: what crosses is a hash, a name, a time or a number."""
    fake = FakeMegingjord(lambda method, url, body: Answer(201, {}))
    smuggled = release(card_uri="https://alviss.sindri.veldris.internal/" + "QUJD" * 40)
    with pytest.raises(ContentLeakError):
        link(bundle, fake).register(smuggled)
    assert fake.requests == []


# -- the anchor state an operator sees ------------------------------------------


@pytest.mark.parametrize(
    ("outcome", "problem", "before", "state"),
    [
        ("countersigned", "", False, "ANCHORED"),
        ("duplicate", "", True, "ANCHORED"),
        ("rejected", "site-not-anchorable", True, "QUARANTINED"),
        ("diverged", "fork-detected", True, "DIVERGED"),
        ("rejected", "chain-discontinuity", True, "DIVERGED"),
        ("rejected", "sequence-regression", True, "DIVERGED"),
        ("rejected", "unreachable", True, "PARTITIONED"),
        ("rejected", "certificate-invalid", True, "PARTITIONED"),
        # Accepted and waiting on MEGINGJORD's signing key: no change of standing.
        ("rejected", "pending", True, "ANCHORED"),
        ("rejected", "pending", False, "UNANCHORED"),
    ],
)
def test_the_anchor_state_follows_megingjords_answer(
    outcome: str, problem: str, before: bool, state: str
) -> None:
    from draupnir.core.domain.sites import RELEASE_PERMITTING_STATES, anchor_state_after

    found = anchor_state_after(outcome, problem, anchored_before=before)
    assert str(found) == state
    # Only anchored releases; a quarantine is never a state that may.
    assert (found in RELEASE_PERMITTING_STATES) == (state == "ANCHORED")
