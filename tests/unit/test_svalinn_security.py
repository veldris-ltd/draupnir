"""Secrets, scanning, egress, the sandbox, integrity and the crypto envelope.

AC-S1: altering one byte of a base weight file fails the next load.
AC-S3: the teacher-model destination is absent and a call to it is refused.
AC-S6: a planted test secret in a checkpoint blocks registration.
AC-S7: an unsigned plug-in fails to load.
AC-S11: an executor attempting an outbound connection fails, and it is logged.
AC-S16: the cryptographic inventory maps every entry to guidance.
AC-S17: two signatures of different algorithms, verifying against either.
AC-S18: no release metadata reaches any external transparency log.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import ec, ed25519

from draupnir.svalinn import egress, integrity, inventory, pki, sandbox, scanning, secrets
from draupnir.svalinn.egress import (
    ALLOW_LIST,
    TEACHER_DESTINATION,
    Call,
    EgressBroker,
    UndeclaredDestinationError,
)
from draupnir.svalinn.envelope import (
    SUPPORTED,
    Algorithm,
    EnvelopeError,
    NoAcceptableSignatureError,
    seal,
    verify,
)
from draupnir.svalinn.integrity import HashMismatchError, UnverifiableArtefactError
from draupnir.svalinn.pki import (
    PUBLIC_TRANSPARENCY_LOGS,
    TRANSPARENCY_LOG,
    EcdsaP384Signer,
    EcdsaP384Verifier,
    Ed25519Signer,
    Ed25519Verifier,
    PkiVerifier,
    PluginSignature,
)
from draupnir.svalinn.sandbox import SandboxError
from draupnir.svalinn.scanning import SecretDetectedError
from draupnir.svalinn.secrets import (
    REDACTED,
    LeaseExpiredError,
    SecretMaterialisedError,
    SecretsBroker,
    SecretsError,
)

AT = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)

#: A plausible-looking test credential. Not a real one, and shaped so the
#: scanner's `aws-access-key-id` pattern matches it.
PLANTED = "AKIAIOSFODNN7EXAMPLE"


# ---------------------------------------------------------------------------
# AC-S6: the secrets broker and the pre-registration scan
# ---------------------------------------------------------------------------


@pytest.fixture
def broker() -> SecretsBroker:
    """A broker holding one secret."""
    return SecretsBroker(store={"hub-token": "hf_" + "a" * 34})


def test_a_lease_never_serialises_its_value(broker: SecretsBroker) -> None:
    """The value has one way out, and it is named `reveal`."""
    lease = broker.issue("hub-token", run_id="run-1", now=AT)

    assert broker.store["hub-token"] not in repr(lease)
    assert broker.store["hub-token"] not in str(lease)
    assert broker.store["hub-token"] not in str(lease.as_payload())
    assert REDACTED in repr(lease)


def test_the_value_comes_out_only_through_reveal(broker: SecretsBroker) -> None:
    lease = broker.issue("hub-token", run_id="run-1", now=AT)

    assert lease.reveal(AT) == broker.store["hub-token"]


def test_a_lease_expires(broker: SecretsBroker) -> None:
    """A token that outlives its job is a token somebody can use afterwards."""
    lease = broker.issue("hub-token", run_id="run-1", now=AT)

    with pytest.raises(LeaseExpiredError):
        lease.reveal(AT + timedelta(hours=2))


def test_a_lease_longer_than_the_ceiling_is_refused(broker: SecretsBroker) -> None:
    """The phrase "short lived" stops meaning anything once it is stretched."""
    with pytest.raises(SecretsError, match="length of a training run"):
        broker.issue("hub-token", run_id="run-1", now=AT, ttl=timedelta(days=1))


def test_a_missing_secret_is_refused_rather_than_substituted(broker: SecretsBroker) -> None:
    with pytest.raises(SecretsError, match="refused rather than substituted"):
        broker.issue("nonexistent", run_id="run-1", now=AT)


def test_the_job_environment_carries_references_never_values(broker: SecretsBroker) -> None:
    """The requirement: never written into a job environment file."""
    lease = broker.issue("hub-token", run_id="run-1", now=AT)

    environment = secrets.brokered_environment([lease])

    assert environment == {"DRAUPNIR_LEASE_HUB-TOKEN": lease.reference}
    assert broker.store["hub-token"] not in str(environment)


def test_a_secret_in_a_rendered_plan_is_caught_before_submission(
    broker: SecretsBroker,
) -> None:
    """The check is on the serialised form.

    A secret interpolated into a command string is the case a values-only
    check misses, and the one that actually happens.
    """
    plan = {"command": ["train", f"--token={broker.store['hub-token']}"]}

    with pytest.raises(SecretMaterialisedError, match="hub-token"):
        broker.assert_no_secrets("the rendered job plan", plan)


def test_a_clean_plan_passes(broker: SecretsBroker) -> None:
    broker.assert_no_secrets("the rendered job plan", {"command": ["train"]})


def test_executor_output_is_redacted_on_the_way_in(broker: SecretsBroker) -> None:
    line = f"loading with token {broker.store['hub-token']} ok"

    assert broker.redact(line) == f"loading with token {REDACTED} ok"


def test_a_planted_secret_in_a_checkpoint_blocks_registration(tmp_path: Path) -> None:
    """AC-S6 and the prompt's exit condition, stated directly."""
    checkpoint = tmp_path / "checkpoint"
    checkpoint.mkdir()
    (checkpoint / "model.safetensors").write_bytes(
        b"\x00" * 4096 + PLANTED.encode() + b"\x00" * 4096
    )
    (checkpoint / "config.json").write_text("{}", encoding="utf-8")

    with pytest.raises(SecretDetectedError) as raised:
        scanning.scan_before_registration(checkpoint)

    assert "registration is blocked" in str(raised.value)
    assert "aws-access-key-id" in str(raised.value)
    assert raised.value.findings[0].path == "model.safetensors"


def test_the_scan_report_never_quotes_what_it_found(tmp_path: Path) -> None:
    """A report that carries the secret has to be handled as a secret."""
    target = tmp_path / "train.log"
    target.write_text(f"exporting {PLANTED} now", encoding="utf-8")

    result = scanning.scan(tmp_path)

    assert not result.clean
    assert PLANTED not in str(result.as_payload())
    assert PLANTED not in str(SecretDetectedError(result.findings))
    assert len(result.findings[0].digest) == 16


def test_a_secret_straddling_a_chunk_boundary_is_still_found(tmp_path: Path) -> None:
    """Without the overlap there is one blind spot per chunk."""
    target = tmp_path / "weights.bin"
    chunk = 1024
    padding = chunk - len(PLANTED) // 2
    target.write_bytes(b"\x00" * padding + PLANTED.encode() + b"\x00" * chunk)

    findings, _ = scanning.scan_file(target, chunk=chunk)

    assert [item.pattern for item in findings] == ["aws-access-key-id"]


def test_a_clean_checkpoint_registers(tmp_path: Path) -> None:
    (tmp_path / "model.safetensors").write_bytes(b"\x00" * 8192)

    result = scanning.scan_before_registration(tmp_path)

    assert result.clean
    assert result.files_scanned == 1


def test_a_lease_reference_in_an_artefact_is_a_finding(tmp_path: Path) -> None:
    """Not a credential, but evidence the broker's contract was broken."""
    (tmp_path / "trainer_state.json").write_text(
        '{"env": "lease:AbCdEfGhIjKlMnOpQr"}', encoding="utf-8"
    )

    result = scanning.scan(tmp_path)

    assert [item.pattern for item in result.findings] == ["draupnir-lease-reference"]


def test_every_pattern_carries_a_remediation() -> None:
    """A finding without a next step gets waved through by whoever is on shift."""
    for pattern in scanning.PATTERNS:
        assert pattern.remediation


# ---------------------------------------------------------------------------
# AC-S3 and AC-S11: the egress broker
# ---------------------------------------------------------------------------


def test_the_teacher_destination_is_not_allow_listed() -> None:
    """AC-S3, and the test that fails if anybody adds it.

    Distillation is out of scope for Release 1 (SAD Q3, threat T3). The
    destination becoming allow-listed is exactly the change that must not
    happen quietly.
    """
    assert TEACHER_DESTINATION not in egress.allow_listed_hosts()
    assert not any("teacher" in item.host for item in ALLOW_LIST)


def test_a_call_to_the_teacher_destination_fails_with_a_logged_refusal() -> None:
    """AC-S3's second clause."""
    broker = EgressBroker()
    call = Call(
        url=f"https://{TEACHER_DESTINATION}/v1/generate",
        purpose="distillation",
        run_id="run-1",
        approving_policy="none",
        requested_at=AT,
    )

    with pytest.raises(UndeclaredDestinationError, match="not in the allow list"):
        broker.request(call)

    assert len(broker.refusals) == 1
    logged = broker.log()[0]
    assert logged["permitted"] is False
    assert logged["host"] == TEACHER_DESTINATION
    assert logged["runId"] == "run-1"


def test_an_allow_listed_destination_is_permitted_and_logged() -> None:
    broker = EgressBroker()
    record = broker.request(
        Call(
            url="https://megingjord.veldris.internal/v1/anchors",
            purpose="chain-head anchoring",
            run_id=None,
            approving_policy="federation/2026.01",
            requested_at=AT,
        )
    )

    assert record.permitted
    assert broker.log()[0]["approvingPolicy"] == "federation/2026.01"


def test_a_destination_approved_for_one_purpose_is_not_approved_for_another() -> None:
    broker = EgressBroker()

    record = broker.check(
        Call(
            url="https://huggingface.co/api/models",
            purpose="uploading a corpus",
            run_id="run-1",
            approving_policy="federation/2026.01",
            requested_at=AT,
        )
    )

    assert not record.permitted
    assert "approved under" in record.reason


def test_a_call_that_declares_nothing_is_refused() -> None:
    """A call the broker cannot describe is one nobody can account for."""
    with pytest.raises(egress.UndeclaredCallError, match="approving_policy"):
        Call(
            url="https://pypi.org",
            purpose="deps",
            run_id=None,
            approving_policy="",
            requested_at=AT,
        )


def test_every_allow_list_entry_names_an_approving_policy() -> None:
    """An entry with no approving policy is one somebody added."""
    for destination in ALLOW_LIST:
        assert destination.approving_policy
        assert destination.purpose


def test_the_log_carries_no_headers_or_bodies() -> None:
    broker = EgressBroker()
    broker.check(
        Call(
            url="https://pypi.org/simple",
            purpose="dependency resolution at image build time",
            run_id=None,
            approving_policy="supply-chain/2026.01",
            requested_at=AT,
        )
    )

    assert set(broker.log()[0]) == {
        "event",
        "permitted",
        "host",
        "scheme",
        "purpose",
        "runId",
        "approvingPolicy",
        "requestedAt",
        "reason",
    }


# ---------------------------------------------------------------------------
# AC-S7 and AC-S11: the executor sandbox
# ---------------------------------------------------------------------------


def test_an_executor_runs_rootless_with_no_network_and_read_only_artefacts() -> None:
    """Threat T7 and threat T11, in one profile."""
    profile = sandbox.for_job(workdir="/work", artefacts=[("/vault/base", "/artefacts/base")])

    assert profile.uid == sandbox.NONROOT_UID
    assert profile.uid != 0
    assert profile.network == "none"
    assert profile.capabilities == frozenset()
    assert profile.no_new_privileges
    assert profile.read_only_root
    assert all(item.read_only for item in profile.artefacts)
    assert sandbox.violations(profile) == ()


def test_there_is_no_argument_that_gives_an_executor_a_network() -> None:
    """Every weakening is an absence, not a defaulted field.

    A profile with an `allow_network` argument is a profile that gets relaxed
    for one job that needed it, and the relaxation outlives the job.
    """
    import dataclasses

    fields = {item.name for item in dataclasses.fields(sandbox.SandboxProfile)}

    assert fields & {"network", "privileged", "user", "uid", "capabilities"} == set()


def test_a_writable_artefact_mount_is_refused() -> None:
    """A job that can write to the artefact store reaches threat T8 from inside."""
    with pytest.raises(SandboxError, match="threat T8 reached from inside"):
        sandbox.SandboxProfile(
            workdir="/work",
            artefacts=(sandbox.Mount(source="/vault", target="/artefacts", read_only=False),),
        )


def test_a_credential_in_the_executor_environment_is_refused() -> None:
    """A tripwire on the way in; `scanning` is the thorough one on the way out."""
    with pytest.raises(SandboxError, match="never written into a job environment"):
        sandbox.for_job(workdir="/work", environment={"HF_TOKEN": "hf_" + "a" * 34})


def test_a_lease_reference_in_the_environment_is_fine() -> None:
    profile = sandbox.for_job(
        workdir="/work", environment={"DRAUPNIR_LEASE_HUB-TOKEN": "lease:abc123"}
    )

    assert profile.environment["DRAUPNIR_LEASE_HUB-TOKEN"] == "lease:abc123"


def test_the_rendered_runtime_arguments_carry_the_properties() -> None:
    profile = sandbox.for_job(workdir="/work", artefacts=[("/vault", "/artefacts")])

    docker = profile.render(sandbox.Runtime.DOCKER)
    nspawn = profile.render(sandbox.Runtime.SYSTEMD_NSPAWN)

    assert "--network=none" in docker
    assert "--cap-drop=ALL" in docker
    assert "--volume=/vault:/artefacts:ro" in docker
    assert "--private-network" in nspawn
    assert "--bind-ro=/vault:/artefacts" in nspawn


def test_a_blocked_outbound_attempt_appears_in_the_log() -> None:
    """AC-S11's second clause."""
    lines = sandbox.blocked(
        [sandbox.BlockedConnection(run_id="run-1", destination="1.1.1.1:443", at=AT.isoformat())]
    )

    assert lines[0]["event"] == "egress.blocked"
    assert lines[0]["layer"] == "executor-sandbox"
    assert lines[0]["runId"] == "run-1"


def test_a_profile_from_elsewhere_is_held_to_the_same_statement() -> None:
    """`violations` exists for a profile this module did not build."""

    class Loose:
        """A profile from a runbook that got it wrong."""

        network = "bridge"
        uid = 0
        capabilities = frozenset({"CAP_SYS_ADMIN"})
        no_new_privileges = False
        read_only_root = False
        artefacts = ()

    found = sandbox.violations(Loose())  # type: ignore[arg-type]

    assert len(found) == 5
    assert any("threat T11" in item for item in found)
    assert any("threat T7" in item for item in found)


# ---------------------------------------------------------------------------
# AC-S1: load-time integrity
# ---------------------------------------------------------------------------


def test_altering_one_byte_of_a_base_weight_fails_the_next_load(tmp_path: Path) -> None:
    """AC-S1, stated directly."""
    weights = tmp_path / "base"
    weights.mkdir()
    (weights / "model.safetensors").write_bytes(b"weights" * 1000)
    expected = integrity.digest_of(weights)

    integrity.verify_before_load(
        weights, artefact="hodd://sindri/models/core/base", expected=expected, at=AT
    )

    # One byte.
    (weights / "model.safetensors").write_bytes(b"weightt" + b"weights" * 999)

    with pytest.raises(HashMismatchError) as raised:
        integrity.verify_before_load(
            weights, artefact="hodd://sindri/models/core/base", expected=expected, at=AT
        )

    assert raised.value.expected == expected
    assert raised.value.observed != expected
    assert "The run does not start" in str(raised.value)


def test_the_refusal_produces_a_ledger_entry(tmp_path: Path) -> None:
    """AC-S1's second clause: a tamper that is not recorded is not investigated."""
    target = tmp_path / "weights.safetensors"
    target.write_bytes(b"tampered")

    result = integrity.verify(
        target, artefact="hodd://sindri/base", expected="a" * 64, at=AT, run_id="run-1"
    )

    assert not result.matches
    payload = result.refusal().as_payload()
    assert payload["event"] == "integrity.refused"
    assert payload["threat"] == "T1"
    assert payload["outcome"] == "the run did not start"
    assert payload["runId"] == "run-1"


def test_a_specification_with_no_expected_hash_is_refused(tmp_path: Path) -> None:
    """Treating an unverifiable load as verified is how the control is lost."""
    target = tmp_path / "weights.safetensors"
    target.write_bytes(b"anything")

    with pytest.raises(UnverifiableArtefactError, match="refused rather than treated"):
        integrity.verify_before_load(target, artefact="hodd://x", expected=None, at=AT)


def test_renaming_a_shard_changes_the_digest(tmp_path: Path) -> None:
    """Renaming shards changes which weights load."""
    root = tmp_path / "model"
    root.mkdir()
    (root / "shard-0.safetensors").write_bytes(b"one")
    (root / "shard-1.safetensors").write_bytes(b"two")
    before = integrity.hash_tree(root)

    (root / "shard-0.safetensors").rename(root / "shard-2.safetensors")

    assert integrity.hash_tree(root) != before


def test_every_declared_input_is_verified_not_only_the_base(tmp_path: Path) -> None:
    """A poisoned corpus is the same threat with a different artefact kind."""
    base = tmp_path / "base.safetensors"
    base.write_bytes(b"base")
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_bytes(b"corpus")

    results = integrity.verify_inputs(
        {
            "base": (base, integrity.hash_file(base)),
            "dataset": (corpus, integrity.hash_file(corpus)),
        },
        at=AT,
    )

    assert {item.artefact for item in results} == {"base", "dataset"}


# ---------------------------------------------------------------------------
# AC-S17: the crypto-agile signature envelope
# ---------------------------------------------------------------------------


@pytest.fixture
def signers() -> tuple[Ed25519Signer, EcdsaP384Signer]:
    """One signer of each Release 1 algorithm."""
    return (
        Ed25519Signer(signing_key=ed25519.Ed25519PrivateKey.generate(), key_id="veldris-ed25519-1"),
        EcdsaP384Signer(
            signing_key=ec.generate_private_key(ec.SECP384R1()),
            key_id="veldris-ecdsa-1",
        ),
    )


def test_an_envelope_carries_two_algorithms_and_verifies_against_either(
    signers: tuple[Ed25519Signer, EcdsaP384Signer],
) -> None:
    """AC-S17, stated directly."""
    ed, ecdsa = signers
    envelope = seal({"artefact": "a" * 64, "model": "cim-gbr"}, [ed, ecdsa], signed_at=AT)

    assert envelope.algorithms == (Algorithm.ECDSA_P384, Algorithm.ED25519)

    ed_only = Ed25519Verifier(trust_store={ed.key_id: ed.public_key()})
    ecdsa_only = EcdsaP384Verifier(trust_store={ecdsa.key_id: ecdsa.public_key()})

    assert verify(envelope, [ed_only]).algorithm is Algorithm.ED25519
    assert verify(envelope, [ecdsa_only]).algorithm is Algorithm.ECDSA_P384
    assert verify(envelope, [ed_only, ecdsa_only]).verified_by == (
        Algorithm.ECDSA_P384,
        Algorithm.ED25519,
    )


def test_a_verifier_holding_only_the_old_algorithm_can_still_verify(
    signers: tuple[Ed25519Signer, EcdsaP384Signer],
) -> None:
    """What makes a migration incremental. Requiring all would break it."""
    ed, ecdsa = signers
    envelope = seal({"artefact": "a" * 64}, [ed, ecdsa], signed_at=AT)

    assert verify(envelope, [Ed25519Verifier(trust_store={ed.key_id: ed.public_key()})])


def test_a_tampered_payload_verifies_under_neither(
    signers: tuple[Ed25519Signer, EcdsaP384Signer],
) -> None:
    import dataclasses

    ed, ecdsa = signers
    envelope = seal({"artefact": "a" * 64}, [ed, ecdsa], signed_at=AT)
    tampered = dataclasses.replace(envelope, payload={"artefact": "b" * 64})

    with pytest.raises(NoAcceptableSignatureError):
        verify(
            tampered,
            [
                Ed25519Verifier(trust_store={ed.key_id: ed.public_key()}),
                EcdsaP384Verifier(trust_store={ecdsa.key_id: ecdsa.public_key()}),
            ],
        )


def test_an_algorithm_the_verifier_does_not_accept_is_not_enough(
    signers: tuple[Ed25519Signer, EcdsaP384Signer],
) -> None:
    """Otherwise a withdrawn algorithm keeps verifying, and withdrawal is the point."""
    ed, _ = signers
    envelope = seal({"artefact": "a" * 64}, [ed], signed_at=AT)
    verifier = Ed25519Verifier(trust_store={ed.key_id: ed.public_key()})

    with pytest.raises(NoAcceptableSignatureError, match="deployment problem"):
        verify(envelope, [verifier], accepted=[Algorithm.ECDSA_P384])


def test_signatures_are_a_list_from_the_first_version(
    signers: tuple[Ed25519Signer, EcdsaP384Signer],
) -> None:
    """Decision S10: adding an algorithm must not be a format change."""
    ed, _ = signers
    single = seal({"artefact": "a" * 64}, [ed], signed_at=AT)

    assert isinstance(single.as_payload()["signatures"], list)
    assert "signature" not in single.as_payload()


def test_an_envelope_round_trips_through_its_published_form(
    signers: tuple[Ed25519Signer, EcdsaP384Signer],
) -> None:
    ed, ecdsa = signers
    envelope = seal({"artefact": "a" * 64}, [ed, ecdsa], signed_at=AT)

    rebuilt = type(envelope).from_payload(envelope.as_payload())

    assert rebuilt.digest() == envelope.digest()
    assert verify(rebuilt, [Ed25519Verifier(trust_store={ed.key_id: ed.public_key()})])


def test_an_unknown_schema_is_refused_rather_than_read_optimistically() -> None:
    with pytest.raises(EnvelopeError, match="refused rather than read"):
        from draupnir.svalinn.envelope import Envelope

        Envelope.from_payload({"schema": "someone-elses/v9", "payload": {}, "signatures": []})


def test_two_signatures_of_one_algorithm_are_refused(
    signers: tuple[Ed25519Signer, EcdsaP384Signer],
) -> None:
    ed, _ = signers
    envelope = seal({"artefact": "a" * 64}, [ed], signed_at=AT)

    with pytest.raises(EnvelopeError, match="two signatures of the same algorithm"):
        type(envelope)(payload=envelope.payload, signatures=envelope.signatures * 2)


def test_the_post_quantum_algorithm_is_declared_and_not_implemented() -> None:
    """The honest statement: the envelope is ready, the library estate is not."""
    assert Algorithm.ML_DSA_65 not in SUPPORTED
    assert Algorithm.ED25519 in SUPPORTED


# ---------------------------------------------------------------------------
# AC-S7 and AC-S18: plug-in verification and no public transparency log
# ---------------------------------------------------------------------------


def test_an_unsigned_plugin_fails_to_load() -> None:
    """AC-S7, first clause. Fails closed."""
    status = PkiVerifier().verify("veldris-draupnir-slurm", "1.0.0")

    assert not status.verified
    assert "An unsigned plug-in does not load" in (status.reason or "")


def test_a_plugin_signed_by_an_unknown_key_fails_to_load() -> None:
    key = ed25519.Ed25519PrivateKey.generate()
    verifier = PkiVerifier()
    verifier.register(
        PluginSignature(
            distribution="veldris-draupnir-slurm",
            version="1.0.0",
            key_id="somebody-else",
            signature=key.sign(bytes.fromhex("aa" * 32)).hex(),
            signed_at=AT,
            sha256="aa" * 32,
        )
    )

    status = verifier.verify("veldris-draupnir-slurm", "1.0.0")

    assert not status.verified
    assert "not in the trust store" in (status.reason or "")


def test_a_correctly_signed_plugin_verifies() -> None:
    key = ed25519.Ed25519PrivateKey.generate()
    digest = "aa" * 32
    verifier = PkiVerifier(trust_store={"veldris-plugin-1": key.public_key()})
    verifier.register(
        PluginSignature(
            distribution="veldris-draupnir-slurm",
            version="1.0.0",
            key_id="veldris-plugin-1",
            signature=key.sign(bytes.fromhex(digest)).hex(),
            signed_at=AT,
            sha256=digest,
        )
    )

    status = verifier.verify("veldris-draupnir-slurm", "1.0.0")

    assert status.verified
    assert status.signer == "veldris-plugin-1"


def test_a_modified_distribution_fails_verification() -> None:
    key = ed25519.Ed25519PrivateKey.generate()
    verifier = PkiVerifier(trust_store={"veldris-plugin-1": key.public_key()})
    verifier.register(
        PluginSignature(
            distribution="veldris-draupnir-slurm",
            version="1.0.0",
            key_id="veldris-plugin-1",
            signature=key.sign(bytes.fromhex("aa" * 32)).hex(),
            signed_at=AT,
            sha256="bb" * 32,  # The contents changed after signing.
        )
    )

    status = verifier.verify("veldris-draupnir-slurm", "1.0.0")

    assert not status.verified
    assert "modified since signing" in (status.reason or "")


def test_the_transparency_log_is_internal() -> None:
    """AC-S18 and Decision S9: no release metadata to an external log."""
    assert pki.transparency_log_is_internal()
    assert "veldris.internal" in TRANSPARENCY_LOG

    for public_log in PUBLIC_TRANSPARENCY_LOGS:
        assert not pki.transparency_log_is_internal(f"https://{public_log}")


def test_no_public_transparency_log_is_reachable() -> None:
    """AC-S18, structurally: the capability is absent, not merely unused.

    No public log appears in the egress allow list, so a call to one would be
    refused by the broker even if some dependency tried.
    """
    hosts = set(egress.allow_listed_hosts())

    assert hosts & PUBLIC_TRANSPARENCY_LOGS == set()


def test_this_package_imports_no_sigstore_client() -> None:
    """Decision S9. The capability is not present to be misconfigured."""
    import ast

    import draupnir.svalinn as package

    banned = ("sigstore", "rekor", "fulcio", "cosign")
    offences: list[str] = []

    for path in sorted(Path(package.__file__).parent.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.alias):
                offences += [
                    f"{path.name}:{node.name}" for word in banned if word in node.name.lower()
                ]
            elif isinstance(node, ast.ImportFrom) and node.module:
                offences += [
                    f"{path.name}:{node.module}" for word in banned if word in node.module.lower()
                ]

    assert not offences, (
        f"{', '.join(offences)} imports a public transparency log client. Decision S9 "
        "replaced public Sigstore with an internal PKI and a self-hosted Rekor: "
        "publishing the shape of a customer's release schedule to a public log is a "
        "poor fit for a company whose position rests on sovereignty (AC-S18)."
    )


# ---------------------------------------------------------------------------
# AC-S16: the cryptographic inventory
# ---------------------------------------------------------------------------


def test_every_inventory_entry_maps_to_guidance() -> None:
    """AC-S16, stated directly."""
    built = inventory.build(AT)

    assert built.rows
    for entry in built.rows:
        assert entry.reference, f"{entry.algorithm} cites no guidance"
        assert (
            "NCSC" in entry.reference or "ISO/IEC" in entry.reference or "FIPS" in entry.reference
        )


def test_the_inventory_lists_every_algorithm_the_envelope_knows() -> None:
    """An algorithm in the code and not in the inventory is what this catches."""
    listed = {
        "".join(c for c in entry.algorithm.lower() if c.isalnum()) for entry in inventory.entries()
    }

    for algorithm in Algorithm:
        assert "".join(c for c in str(algorithm).lower() if c.isalnum()) in listed


def test_an_algorithm_missing_from_the_inventory_fails_the_build() -> None:
    thin = [entry for entry in inventory.entries() if "Ed25519" not in entry.algorithm]

    with pytest.raises(inventory.InventoryError, match="AC-S16 exists to catch"):
        inventory.build(AT, thin)


def test_a_row_with_no_reference_is_refused() -> None:
    with pytest.raises(inventory.InventoryError, match="cites no guidance"):
        inventory.Entry(
            purpose="signing", algorithm="Ed25519", key_bits=256, module="x", reference=""
        )


def test_the_inventory_states_the_assurance_position_plainly() -> None:
    """SAD 9.5: it should be presented as what it is."""
    built = inventory.build(AT)

    assert "not a validation scheme comparable to CMVP" in built.assurance_position
    assert "weaker assertion than 'FIPS validated'" in built.assurance_position


def test_the_inventory_records_tls_1_3_only() -> None:
    payload = inventory.build(AT).as_payload()

    assert payload["transportPolicy"] == "TLS 1.3 only"
    assert "self-hosted" in str(payload["transparencyLog"])


def test_the_inventory_renders_as_a_build_artefact() -> None:
    built = inventory.build(AT)

    assert built.to_markdown().startswith("# Cryptographic inventory")
    assert "Ed25519" in built.to_json()
    assert built.declared_not_implemented
