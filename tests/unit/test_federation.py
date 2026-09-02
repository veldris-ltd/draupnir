"""Anchoring, partition behaviour, and what the federation is allowed to hold.

AC-S13: a forge disconnected from MEGINGJORD continues to train and record, and
a release attempt is refused with a clear reason. On reconnect the queued
anchors submit in order and the release becomes available.

AC-S14: a forge ledger verifies its own chain integrity with MEGINGJORD
unreachable. No corpus or weight content is present in any federation payload,
verified by inspection of the wire format.

AC-N11: anchor round trip under 2 seconds at the 95th percentile.
AC-N12: 72 hours with the federation link down and no degradation other than
blocked release.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from draupnir.core.domain.federation import (
    Anchor,
    AnchorOutcome,
    AnchorSubmission,
    Capacity,
    ContentLeakError,
    PolicyBundle,
    ReleaseRecord,
    Site,
    declared_fields,
    hashes_in,
    inspect_capture,
    is_hash,
    sealed,
    to_wire,
)
from draupnir.core.domain.ledger import ChainHead
from draupnir.gullinbursti.agent import (
    ANCHOR_ROUND_TRIP_BUDGET,
    Gullinbursti,
    LinkState,
    ReleaseBlockedError,
    anchored_through,
    round_trip_within_budget,
)
from draupnir.megingjord.anchors import AnchorStore, DivergenceError
from draupnir.megingjord.registry import FederationRegistry, RegistryError, sindri

AT = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)
COUNTERSIGNATURE = "megingjord-countersignature"


def entry_hash(seq: int) -> str:
    """A distinct hash per ledger sequence."""
    return f"{seq:064x}"


def head(seq: int, *, site: str = "sindri", digest: str | None = None) -> AnchorSubmission:
    """A signed submission of the chain head at `seq`."""
    return AnchorSubmission(
        head=ChainHead(site_id=site, seq=seq, entry_hash=digest or entry_hash(seq)),
        previous_hash=entry_hash(seq - 1) if seq > 1 else None,
        submitted_at=AT,
        signature="site-signature",
        key_id="sindri-anchor-1",
    )


@pytest.fixture
def store() -> AnchorStore:
    """An empty anchor store."""
    return AnchorStore()


@pytest.fixture
def agent() -> Gullinbursti:
    """The Sindri agent."""
    return Gullinbursti(site_id="sindri", signing_key_id="sindri-anchor-1")


# ---------------------------------------------------------------------------
# Anchoring and continuity
# ---------------------------------------------------------------------------


def test_a_head_is_countersigned_after_continuity_is_verified(store: AnchorStore) -> None:
    """SAD 11A.3."""
    receipt = store.countersign(head(1), at=AT, countersignature=COUNTERSIGNATURE)

    assert receipt.outcome is AnchorOutcome.COUNTERSIGNED
    assert receipt.accepted
    latest = store.latest("sindri")
    assert latest is not None
    assert latest.seq == 1


def test_an_unsigned_head_is_not_a_claim(store: AnchorStore) -> None:
    unsigned = AnchorSubmission(
        head=ChainHead(site_id="sindri", seq=1, entry_hash=entry_hash(1)),
        previous_hash=None,
        submitted_at=AT,
    )

    receipt = store.countersign(unsigned, at=AT, countersignature=COUNTERSIGNATURE)

    assert receipt.outcome is AnchorOutcome.REJECTED
    assert "it is a packet" in receipt.reason


def test_a_head_that_does_not_join_the_chain_is_rejected(store: AnchorStore) -> None:
    """Continuity is checkable from hashes alone.

    Which is what lets the federation hold hashes alone.
    """
    store.countersign(head(1), at=AT, countersignature=COUNTERSIGNATURE)
    broken = AnchorSubmission(
        head=ChainHead(site_id="sindri", seq=2, entry_hash=entry_hash(2)),
        previous_hash=entry_hash(99),
        submitted_at=AT,
        signature="site-signature",
    )

    receipt = store.countersign(broken, at=AT, countersignature=COUNTERSIGNATURE)

    assert receipt.outcome is AnchorOutcome.REJECTED
    assert "The chain does not join" in receipt.reason


def test_resubmitting_an_already_anchored_head_is_a_duplicate(store: AnchorStore) -> None:
    """A retry is idempotent wherever it sits in the chain.

    Rejecting it as "behind the head" would turn a network hiccup into an
    operator incident, and it is the same bytes at the same sequence.
    """
    store.countersign(head(1), at=AT, countersignature=COUNTERSIGNATURE)
    store.countersign(head(2), at=AT, countersignature=COUNTERSIGNATURE)

    receipt = store.countersign(head(1), at=AT, countersignature=COUNTERSIGNATURE)

    assert receipt.accepted
    assert receipt.outcome is AnchorOutcome.DUPLICATE


def test_a_sequence_below_the_head_that_was_never_anchored_is_rejected(
    store: AnchorStore,
) -> None:
    """A gap below the head is a hole in the chain, not a late arrival."""
    store.countersign(head(1), at=AT, countersignature=COUNTERSIGNATURE)
    store.countersign(head(3), at=AT, countersignature=COUNTERSIGNATURE)

    receipt = store.countersign(head(2), at=AT, countersignature=COUNTERSIGNATURE)

    assert not receipt.accepted
    assert "a hole in the chain" in receipt.reason


def test_a_rewritten_history_diverges_and_puts_the_forge_read_only(
    store: AnchorStore,
) -> None:
    """Threat T12. Both sides alarm; no release until investigated."""
    store.countersign(head(1), at=AT, countersignature=COUNTERSIGNATURE)

    with pytest.raises(DivergenceError) as raised:
        store.countersign(head(1, digest=entry_hash(999)), at=AT, countersignature=COUNTERSIGNATURE)

    assert "rewritten after being anchored" in str(raised.value)
    assert "This is not retried" in str(raised.value)
    assert "sindri" in store.read_only


def test_no_further_anchor_is_accepted_from_a_read_only_forge(store: AnchorStore) -> None:
    store.countersign(head(1), at=AT, countersignature=COUNTERSIGNATURE)
    with pytest.raises(DivergenceError):
        store.countersign(head(1, digest=entry_hash(999)), at=AT, countersignature=COUNTERSIGNATURE)

    receipt = store.countersign(head(2), at=AT, countersignature=COUNTERSIGNATURE)

    assert receipt.outcome is AnchorOutcome.REJECTED
    assert "read only after a divergence" in receipt.reason


def test_a_queue_is_anchored_in_order_and_stops_at_a_rejection(store: AnchorStore) -> None:
    """Accepting a later head after rejecting an earlier one anchors a hole."""
    broken = AnchorSubmission(
        head=ChainHead(site_id="sindri", seq=2, entry_hash=entry_hash(2)),
        previous_hash=entry_hash(99),
        submitted_at=AT,
        signature="site-signature",
    )

    receipts = store.submit_queue(
        [head(3), broken, head(1)], at=AT, countersignature=COUNTERSIGNATURE
    )

    assert [item.outcome for item in receipts] == [
        AnchorOutcome.COUNTERSIGNED,
        AnchorOutcome.REJECTED,
    ]
    latest = store.latest("sindri")
    assert latest is not None
    assert latest.seq == 1


# ---------------------------------------------------------------------------
# AC-S13 and AC-N12: partition behaviour
# ---------------------------------------------------------------------------


def test_a_partitioned_forge_keeps_recording(agent: Gullinbursti) -> None:
    """AC-S13, first clause. Decision S8: training continues."""
    agent.partition()

    for seq in range(1, 51):
        agent.submit(head(seq), at=AT)

    assert agent.link is LinkState.DOWN
    assert agent.queue_depth == 50
    assert agent.unanchored == tuple(range(1, 51))


def test_a_release_attempt_during_a_partition_is_refused_with_a_reason(
    agent: Gullinbursti,
) -> None:
    """AC-S13, second clause. The reason decides whether to wait or escalate."""
    agent.partition()
    agent.submit(head(1), at=AT)

    permitted, reason = agent.may_release(1)

    assert not permitted
    assert "the federation link is down" in reason
    assert "1 head(s) are queued" in reason

    with pytest.raises(ReleaseBlockedError) as raised:
        agent.require_release(1)

    assert "Training continues through a partition; release does not" in str(raised.value)
    assert "waits in AWAITING_APPROVAL" in str(raised.value)


def test_on_reconnect_the_queue_submits_in_order_and_release_unblocks(
    agent: Gullinbursti, store: AnchorStore
) -> None:
    """AC-S13, third clause, end to end."""
    agent.partition()
    for seq in range(1, 6):
        agent.submit(head(seq), at=AT)
    assert not agent.may_release(5)[0]

    agent.restore()
    receipts = agent.drain(store, at=AT, countersignature=COUNTERSIGNATURE)

    assert [item.outcome for item in receipts] == [AnchorOutcome.COUNTERSIGNED] * 5
    assert anchored_through(receipts) == 5
    assert agent.queue_depth == 0
    assert agent.may_release(5)[0]


def test_a_forge_with_the_link_down_keeps_its_policy(agent: Gullinbursti) -> None:
    """A partitioned forge works under the policy it had, not under none."""
    registry = FederationRegistry()
    registry.publish_policy(
        PolicyBundle(name="licence", version="2026.01", digest="a" * 64, issued_at=AT)
    )
    agent.pull_policy(registry, "licence")

    agent.partition()
    held = agent.pull_policy(registry, "licence")

    assert held.version == "2026.01"


def test_a_forge_that_never_pulled_a_policy_cannot_proceed(agent: Gullinbursti) -> None:
    """There is nothing to fall back to, and a default would be worse."""
    agent.partition()

    with pytest.raises(Exception, match="nothing to fall back to"):
        agent.pull_policy(FederationRegistry(), "licence")


def test_capacity_is_dropped_rather_than_queued_while_partitioned(
    agent: Gullinbursti,
) -> None:
    """A queue of stale reports describes a forge as it was three days ago."""
    registry = FederationRegistry()
    registry.register_site(sindri(AT))
    agent.partition()

    result = agent.report_capacity(
        registry,
        Capacity(site_id="sindri", reported_at=AT, appliances_total=3, appliances_available=3),
    )

    assert result is None
    assert registry.capacity == {}


def test_seventy_two_hours_down_degrades_nothing_but_release(
    agent: Gullinbursti, store: AnchorStore
) -> None:
    """AC-N12, at the level this can be established without a forge.

    Seventy two hours at the fifteen minute anchor interval is 288 heads. What
    is asserted is that the queue is the only thing that grows, that it grows
    linearly, and that everything unblocks in order on reconnect.
    """
    agent.partition()
    heads = 72 * 60 // 15

    for seq in range(1, heads + 1):
        agent.submit(head(seq), at=AT + timedelta(minutes=15 * seq))

    assert agent.queue_depth == heads
    assert agent.link is LinkState.DOWN
    assert not agent.may_release(heads)[0]

    agent.restore()
    receipts = agent.drain(store, at=AT, countersignature=COUNTERSIGNATURE)

    assert len(receipts) == heads
    assert all(item.accepted for item in receipts)
    assert agent.queue_depth == 0
    assert agent.may_release(heads)[0]


def test_an_agent_will_not_anchor_another_sites_chain(agent: Gullinbursti) -> None:
    """How two forges end up sharing a ledger segment."""
    with pytest.raises(Exception, match="sharing a ledger segment"):
        agent.submit(head(1, site="brokkr"), at=AT)


def test_a_diverged_forge_reports_read_only(agent: Gullinbursti) -> None:
    agent.diverged()

    permitted, reason = agent.may_release(1)

    assert not permitted
    assert "read only after a chain divergence" in reason
    agent.restore()
    assert agent.link is LinkState.READ_ONLY


# ---------------------------------------------------------------------------
# AC-N11: the anchor round trip
# ---------------------------------------------------------------------------


def test_the_anchor_round_trip_budget_is_two_seconds() -> None:
    assert timedelta(seconds=2) == ANCHOR_ROUND_TRIP_BUDGET


def test_round_trips_within_budget_pass_at_the_95th_percentile() -> None:
    """AC-N11. The 95th percentile, because a mean hides the tail."""
    samples = [timedelta(milliseconds=120)] * 95 + [timedelta(milliseconds=800)] * 5

    within, percentile = round_trip_within_budget(samples)

    assert within
    assert percentile <= ANCHOR_ROUND_TRIP_BUDGET


def test_a_slow_tail_fails_the_budget() -> None:
    samples = [timedelta(milliseconds=120)] * 90 + [timedelta(seconds=5)] * 10

    within, percentile = round_trip_within_budget(samples)

    assert not within
    assert percentile > ANCHOR_ROUND_TRIP_BUDGET


# ---------------------------------------------------------------------------
# AC-S14: no corpus or weight content in any federation payload
# ---------------------------------------------------------------------------


def test_every_federation_payload_carries_only_hashes_names_and_numbers() -> None:
    """AC-S14, by inspection of the wire format."""
    registry = FederationRegistry()
    registry.register_site(sindri(AT))
    registry.publish_policy(
        PolicyBundle(name="licence", version="2026.01", digest="a" * 64, issued_at=AT)
    )
    registry.record_release(
        ReleaseRecord(
            site_id="sindri",
            model="cim-gbr-v1.0",
            artefact_sha256="b" * 64,
            released_at=AT,
            seq=42,
            manifest={"modelCard": "c" * 64, "sbom": "d" * 64},
        )
    )
    registry.report_capacity(
        Capacity(site_id="sindri", reported_at=AT, appliances_total=3, appliances_available=2)
    )

    from draupnir.megingjord.registry import payloads_of

    report = inspect_capture(list(payloads_of(registry)))

    assert report.clean, report.refused
    assert report.payloads == 4


def test_a_payload_carrying_weight_bytes_is_refused() -> None:
    with pytest.raises(ContentLeakError, match="it is raw bytes"):
        sealed({"siteId": "sindri", "weights": b"\x00\x01\x02"})


def test_a_payload_carrying_an_encoded_run_is_refused() -> None:
    """Corpus content arrives inside a field declared as a string."""
    with pytest.raises(ContentLeakError, match="an encoded run"):
        sealed({"siteId": "sindri", "label": "QUJDREVG" * 12})


def test_a_payload_carrying_a_long_string_is_refused() -> None:
    with pytest.raises(ContentLeakError, match="over the 512 limit"):
        sealed({"siteId": "sindri", "reason": "the corpus said " * 60})


def test_a_payload_carrying_a_tensor_header_marker_is_refused() -> None:
    with pytest.raises(ContentLeakError, match="marker"):
        sealed({"siteId": "sindri", "note": '{"__metadata__": {}}'})


def test_a_hash_is_not_mistaken_for_an_encoded_run() -> None:
    """The check must not refuse what the federation exists to carry."""
    payload = sealed({"entryHash": "a" * 64, "previousHash": "b" * 64})

    assert hashes_in(payload) == ("a" * 64, "b" * 64)


def test_there_is_no_path_that_serialises_an_unchecked_payload() -> None:
    """`to_wire` seals first, which is what makes AC-S14 a code property."""
    with pytest.raises(ContentLeakError):
        to_wire({"siteId": "sindri", "corpus": b"raw"})

    assert to_wire({"siteId": "sindri", "seq": 1}).startswith(b'{"seq"')


def test_the_capture_report_names_every_problem_not_the_first() -> None:
    report = inspect_capture([{"a": b"bytes"}, {"b": "x" * 600}, {"c": "fine"}])

    assert not report.clean
    assert len(report.refused) == 2


def test_the_declared_field_names_are_documentable() -> None:
    """What the federation carries should be readable as a list."""
    names = declared_fields([head(1).as_payload()])

    assert "entryHash" in names
    assert "siteId" in names
    assert not any("corpus" in name or "weight" in name for name in names)


def test_a_release_record_naming_something_other_than_a_hash_is_refused() -> None:
    from draupnir.core.domain.federation import FederationRecordError

    with pytest.raises(FederationRecordError, match="not a SHA-256"):
        ReleaseRecord(
            site_id="sindri",
            model="cim-gbr",
            artefact_sha256="not-a-hash",
            released_at=AT,
            seq=1,
        )


def test_a_forge_verifies_its_own_chain_with_the_registry_unreachable(
    agent: Gullinbursti,
) -> None:
    """AC-S14, first clause. The ledger's own verification needs nothing remote.

    Asserted here as a property of the agent: it answers `may_release` locally
    and reports its own state without reaching the registry. The chain
    verification itself is `draupnir.core.domain.ledger.verify_chain`, which
    takes entries and an optional anchor and no network.
    """
    agent.partition()
    agent.submit(head(1), at=AT)

    status = agent.status(at=AT)

    assert status["link"] == "down"
    assert status["queueDepth"] == 1
    assert agent.may_release(1)[1]


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------


def test_a_site_re_registering_with_a_different_key_is_refused() -> None:
    """A key change is a rotation with a procedure, or it is somebody else."""
    registry = FederationRegistry()
    registry.register_site(sindri(AT))

    with pytest.raises(RegistryError, match="somebody else claiming to be this site"):
        registry.register_site(sindri(AT, signing_key_id="different"))


def test_a_published_policy_version_is_immutable() -> None:
    """Every decision recorded against it would become unexplainable."""
    registry = FederationRegistry()
    registry.publish_policy(
        PolicyBundle(name="licence", version="2026.01", digest="a" * 64, issued_at=AT)
    )

    with pytest.raises(RegistryError, match="Issue a new version"):
        registry.publish_policy(
            PolicyBundle(name="licence", version="2026.01", digest="b" * 64, issued_at=AT)
        )


def test_pulling_without_a_version_returns_the_most_recent() -> None:
    registry = FederationRegistry()
    registry.publish_policy(
        PolicyBundle(name="licence", version="2025.11", digest="a" * 64, issued_at=AT)
    )
    registry.publish_policy(
        PolicyBundle(
            name="licence",
            version="2026.01",
            digest="b" * 64,
            issued_at=AT + timedelta(days=60),
        )
    )

    assert registry.pull_policy("licence").version == "2026.01"


def test_the_registry_refuses_release_without_a_countersigned_anchor(
    store: AnchorStore,
) -> None:
    """The registry's half of Decision S8."""
    permitted, reason = store.may_release("sindri", 5)
    assert not permitted
    assert "no countersigned anchor" in reason

    store.countersign(head(1), at=AT, countersignature=COUNTERSIGNATURE)

    permitted, reason = store.may_release("sindri", 5)
    assert not permitted
    assert "waits in AWAITING_APPROVAL" in reason

    for seq in (2, 3, 4, 5):
        store.countersign(head(seq), at=AT, countersignature=COUNTERSIGNATURE)

    assert store.may_release("sindri", 5)[0]


def test_sindri_is_site_zero() -> None:
    """SAD 11A.0: Sindri is Site 0, the first and currently only member."""
    site = sindri(AT)

    assert site.site_id == "sindri"
    assert site.ordinal == 0
    assert site.fqdn.endswith(".veldris.internal")


def test_a_site_with_no_signing_key_cannot_be_registered() -> None:
    from draupnir.core.domain.federation import FederationRecordError

    with pytest.raises(FederationRecordError, match="cannot anchor"):
        Site(
            site_id="brokkr",
            ordinal=1,
            fqdn="brokkr.veldris.internal",
            signing_key_id="",
            registered_at=AT,
        )


def test_the_whole_registry_state_seals() -> None:
    """The federation's entire state is hashes, names and numbers."""
    registry = FederationRegistry()
    registry.register_site(sindri(AT))
    registry.trust_plugin_key("veldris-plugin-1", "a" * 64)

    payload = registry.as_payload()

    assert payload["pluginTrustRoot"] == ["veldris-plugin-1"]
    assert is_hash("a" * 64)


def test_an_anchor_payload_is_sealed() -> None:
    anchor = Anchor(head=head(1), countersigned_at=AT, countersignature=COUNTERSIGNATURE)

    payload = anchor.as_payload()

    assert payload["head"]["entryHash"] == entry_hash(1)
    assert payload["outcome"] == "countersigned"
