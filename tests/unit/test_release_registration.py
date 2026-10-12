"""Registering a release with MEGINGJORD at publish, and the link it goes over. G8.

`api.registration` turns a release this forge is about to publish into the
record MEGINGJORD registers (VLD-SAD-MEGINGJORD-001 AC-X5), refuses before
sending anything it can already see MEGINGJORD would refuse, and turns
MEGINGJORD's answer into this API's problem with MEGINGJORD's reason kept.
`api.federation` builds the link from the bundle, as both units do.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from cryptography.x509.oid import NameOID

from draupnir.api import federation, registration
from draupnir.api.problems import ProblemError
from draupnir.core.infrastructure.config import Settings
from draupnir.gullinbursti import protocol
from tests.unit.test_megingjord_link import SITE, write_bundle

ARTEFACT = "d" * 64
WHEN = datetime(2026, 10, 12, 9, 30, 15, 250000, tzinfo=UTC)
CONSOLE = "https://alviss.sindri.veldris.internal"


def facts(**changes: Any) -> Any:
    """What `Orchestrator.publication_facts` would say about one release."""
    values: dict[str, Any] = {
        "release_seq": 40,
        "anchored_through": 44,
        "covering_anchor": {"anchored_through": 42, "countersignature": '{"document":{}}'},
        "last_anchoring": None,
    }
    values.update(changes)
    return SimpleNamespace(**values)


LINK: Any = SimpleNamespace(site_id=SITE)


def record(**changes: Any) -> Any:
    values: dict[str, Any] = {
        "link": LINK,
        "artefact": ARTEFACT,
        "facts": facts(),
        "model": "cim-gbr-v1.0",
        "console": CONSOLE,
        "released_at": WHEN,
    }
    values.update(changes)
    return registration.release_record(**values)


# -- the console's address ---------------------------------------------------


def test_the_console_origin_is_configured_or_taken_from_the_sign_in_address() -> None:
    assert registration.origin(Settings(public_origin=f"{CONSOLE}/")) == CONSOLE
    assert registration.origin(Settings(oidc_redirect_uri=f"{CONSOLE}/v1/auth/callback")) == CONSOLE
    assert registration.origin(Settings(public_origin="", oidc_redirect_uri="")) == ""


# -- the record --------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("cim-gbr-v1.0", ("cim-gbr", "1.0")),
        ("CIM-FRA-v2.1.3", ("cim-fra", "2.1.3")),
        ("cim-deu-experiment", ("cim-deu-experiment", ARTEFACT[:12])),
        ("Cim GBR draft_2", ("cim-gbr-draft-2", ARTEFACT[:12])),
    ],
)
def test_a_run_name_is_split_into_megingjords_name_and_version(
    model: str, expected: tuple[str, str]
) -> None:
    assert registration.name_and_version(model, ARTEFACT) == expected


def test_the_record_registers_the_release_at_its_covering_anchor() -> None:
    made = record()

    assert made.site_id == SITE
    assert (made.name, made.version) == ("cim-gbr", "1.0")
    assert made.jurisdiction == "GBR"
    assert made.tier == "tier-a"
    assert str(made.manifest_sha256) == f"sha256:{ARTEFACT}"
    # The first anchor at or beyond the approval, not the latest: the same
    # release registered twice names the same anchor.
    assert made.anchor_seq == 42
    assert made.card_uri == f"{CONSOLE}/v1/releases/{ARTEFACT}/documents/model-card"
    assert made.lineage_uri.endswith("/documents/lineage")
    assert made.released_at == WHEN.replace(microsecond=0)


@pytest.mark.parametrize(
    ("changes", "said"),
    [
        ({"facts": facts(covering_anchor=None)}, "no countersigned receipt covers sequence 40"),
        ({"model": "unnamed-run"}, "names no jurisdiction"),
        ({"model": "cim-zzz-v1.0"}, "ZZZ"),
        ({"console": ""}, "DRAUPNIR_PUBLIC_ORIGIN"),
        ({"console": "http://alviss"}, "https address"),
    ],
)
def test_a_record_megingjord_would_refuse_is_refused_here_first(
    changes: dict[str, Any], said: str
) -> None:
    with pytest.raises(ProblemError) as raised:
        record(**changes)
    assert raised.value.status == 409
    assert raised.value.code == "release-not-registered"
    assert said in (raised.value.detail or "")


# -- registering -------------------------------------------------------------


class Registering:
    """A link that answers registration as told, and remembers what it sent."""

    site_id = SITE

    def __init__(self, answer: Any) -> None:
        self.answer = answer
        self.sent: list[Any] = []

    def register(self, release: Any) -> Any:
        self.sent.append(release)
        return self.answer


def register(link: Any) -> Any:
    return asyncio.run(
        registration.register_release(
            link,
            artefact=ARTEFACT,
            facts=facts(),
            model="cim-gbr-v1.0",
            console=CONSOLE,
            released_at=WHEN,
        )
    )


def test_a_forge_with_no_link_publishes_nothing() -> None:
    with pytest.raises(ProblemError) as raised:
        register(None)
    assert raised.value.code == "federation-unconfigured"
    assert "DRAUPNIR_MEGINGJORD_BUNDLE" in (raised.value.detail or "")


def test_a_registration_comes_back_with_its_posture() -> None:
    posture = {"v": 1, "recorded": True, "profile": "Guided."}
    answer = protocol.Registration("m-1", "r-1", False, posture)
    link = Registering(answer)

    assert register(link) == answer
    assert link.sent[0].anchor_seq == 42


def test_megingjord_refusing_keeps_its_reason() -> None:
    """AC-X6: a quarantined site's release is refused in MEGINGJORD's words."""
    link = Registering(
        protocol.Refusal(
            409,
            protocol.SITE_CANNOT_RELEASE,
            "the site is QUARANTINED: it cannot obtain a receipt, so it cannot release",
        )
    )
    with pytest.raises(ProblemError) as raised:
        register(link)
    assert raised.value.status == 409
    assert raised.value.code == "release-not-registered"
    assert "the site is QUARANTINED" in (raised.value.detail or "")
    assert "Nothing was published" in (raised.value.detail or "")


def test_an_unreachable_megingjord_is_an_outage_not_a_refusal() -> None:
    link = Registering(protocol.Refusal(0, protocol.UNREACHABLE, "the link is down."))
    with pytest.raises(ProblemError) as raised:
        register(link)
    assert raised.value.status == 503
    assert raised.value.code == "federation-unreachable"


def test_the_publication_records_what_megingjord_registered() -> None:
    answer = protocol.Registration("m-1", "r-1", True, {"v": 1, "recorded": False})
    assert registration.registered(answer, facts()) == {
        "model_id": "m-1",
        "receipt_id": "r-1",
        "replay": True,
        "anchor_seq": 42,
        "receipt": '{"document":{}}',
        "estate_posture": {"v": 1, "recorded": False},
    }


# -- why a release is behind --------------------------------------------------


def test_behind_because_quarantined_says_so() -> None:
    said = registration.last_answer(
        facts(
            last_anchoring={
                "outcome": "rejected",
                "problem": protocol.NOT_ANCHORABLE,
                "reason": "MEGINGJORD refused (site-not-anchorable): the site is QUARANTINED",
            }
        )
    )
    assert "will not anchor this site's chain" in said
    assert "QUARANTINED" in said


def test_behind_for_another_reason_gives_megingjords_last_answer() -> None:
    said = registration.last_answer(
        facts(last_anchoring={"outcome": "rejected", "problem": "unreachable", "reason": "down"})
    )
    assert said == " MEGINGJORD's last answer to this site: down"


def test_behind_with_nothing_refused_adds_nothing() -> None:
    assert registration.last_answer(facts()) == ""
    assert (
        registration.last_answer(facts(last_anchoring={"outcome": "countersigned", "reason": "x"}))
        == ""
    )


# -- the link, from the bundle ---------------------------------------------------


def certificate(subject: str, key: Any, issuer: tuple[str, Any] | None = None) -> bytes:
    """A certificate for `key`, self-signed or issued by `issuer`."""
    now = datetime.now(UTC)
    issuer_name, issuer_key = issuer or (subject, key)
    built = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)]))
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer_name)]))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=issuer is None, path_length=None), critical=True)
        .sign(issuer_key, hashes.SHA256())
    )
    return built.public_bytes(Encoding.PEM)


@pytest.fixture
def real_bundle(tmp_path: Path) -> Path:
    """A bundle whose TLS material loads: a CA, and a site certificate it issued."""
    folder = write_bundle(tmp_path / "megingjord", Ed25519PrivateKey.generate())
    authority = ec.generate_private_key(ec.SECP256R1())
    site = ec.generate_private_key(ec.SECP256R1())
    (folder / "console-ca.crt").write_bytes(certificate("console CA", authority))
    (folder / "site.crt").write_bytes(
        certificate("sindri.veldris.internal", site, issuer=("console CA", authority))
    )
    (folder / "site-mtls.key").write_bytes(
        site.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    )
    return folder


def test_no_bundle_is_no_link() -> None:
    assert federation.megingjord_link("") is None
    assert federation.megingjord_link("   ") is None


def test_a_bundle_is_a_link_speaking_for_its_site(real_bundle: Path) -> None:
    link = federation.megingjord_link(real_bundle)
    assert link is not None
    assert link.site_id == SITE
    assert link.bundle.key_id == "site-sindri-2026-10"


def test_the_installers_check_says_who_it_would_speak_for(
    real_bundle: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert federation.main([str(real_bundle)]) == 0
    said = capsys.readouterr().out
    assert "sindri" in said and str(SITE) in said and "site-sindri-2026-10" in said


def test_the_installers_check_says_what_is_wrong(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert federation.main([str(tmp_path)]) == 1
    assert "enrol.sh" in capsys.readouterr().err
    assert federation.main([""]) == 1


def test_an_unusable_certificate_is_said_plainly(real_bundle: Path) -> None:
    (real_bundle / "site-mtls.key").write_text("not a key\n")
    from draupnir.svalinn.transport import TransportError

    with pytest.raises(TransportError):
        federation.megingjord_link(real_bundle)
