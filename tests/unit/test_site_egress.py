"""The two egress allow lists, held against each other. RF-E17.

The finding: two policies govern the same traffic and neither is the authority,
so neither is evidence. What makes it worth a module rather than a note is the
asymmetry in how the two failures present.

A destination the broker refuses produces a log line naming the destination and
the policy. A destination the *router* refuses produces nothing at all here --
the packet leaves ALVISS and is dropped somewhere else, and the library
reports a timeout. One of those gets diagnosed in a minute and the other gets
diagnosed as "the internet is slow".
"""

from __future__ import annotations

import pytest

from draupnir.svalinn import egress, site_egress
from draupnir.svalinn.site_egress import Consumer, Requirement, Verdict

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# The allow list covers the calls that are actually made
# ---------------------------------------------------------------------------


def test_a_redirect_target_is_declared_beside_the_host_that_redirects_to_it() -> None:
    """The original finding, and the reason it mattered.

    `huggingface.co` answers the metadata and redirects the weight download
    elsewhere. An allow list holding only the first refuses the transfer at
    exactly the point where the transfer starts.
    """
    hosts = set(egress.allow_listed_hosts())

    for bare, blob in (
        ("huggingface.co", "cdn-lfs.huggingface.co"),
        ("pypi.org", "files.pythonhosted.org"),
        ("ghcr.io", "pkg-containers.githubusercontent.com"),
        ("gcr.io", "storage.googleapis.com"),
    ):
        assert bare in hosts
        assert blob in hosts, f"{bare} is declared and {blob}, where it serves from, is not"


def test_every_redirect_target_says_what_redirects_to_it() -> None:
    """Otherwise a second entry for one operation looks like duplication."""
    for item in egress.ALLOW_LIST:
        if item.redirect_from:
            assert item.redirect_from in egress.allow_listed_hosts(), (
                f"{item.host} names {item.redirect_from} as its origin, and that host "
                "is not itself declared"
            )


def test_the_base_images_the_build_actually_starts_from_are_declared() -> None:
    """`docker/` names ghcr.io, gcr.io and cgr.dev. None of them is Docker Hub."""
    hosts = set(egress.allow_listed_hosts())

    assert {"ghcr.io", "gcr.io", "cgr.dev"} <= hosts


def test_every_entry_names_a_purpose_and_an_approving_policy() -> None:
    """An entry with neither is a destination somebody added."""
    for item in egress.ALLOW_LIST:
        assert item.purpose, f"{item.host} has no stated purpose"
        assert item.approving_policy, f"{item.host} has no approving policy"


def test_the_federation_destination_says_the_link_is_not_built() -> None:
    """A declared permission for a path that does not exist is worth having.

    It is not worth mistaking for a live one, which is what an entry sitting
    silently in the list alongside four working ones looks like.
    """
    (megingjord,) = [
        item for item in egress.ALLOW_LIST if item.host == "megingjord.veldris.internal"
    ]

    assert megingjord.gap, "the entry does not say the link is gap-listed"
    assert "RF-E21" in megingjord.gap or "not built" in megingjord.gap


def test_internal_destinations_are_marked_as_not_traversing_the_router() -> None:
    """They are on the site fabrics, so the router has no opinion on them.

    Recorded because the reconciliation must tell "the router permits this"
    from "the router is not in the path". Treating the second as the first
    would put internal hostnames in an internet egress policy.
    """
    internal = {item.host for item in egress.ALLOW_LIST if not item.traverses_site_router}

    assert internal == {"megingjord.veldris.internal", "regin.sindri.veldris.internal"}
    assert internal.isdisjoint(site_egress.SITE_ROUTER_POLICY), (
        "an internal hostname appears in the site router's internet allow list"
    )


# ---------------------------------------------------------------------------
# The transcription
# ---------------------------------------------------------------------------


def test_the_transcription_carries_its_provenance() -> None:
    """A transcription without a source and a date cannot be re-checked."""
    assert site_egress.SOURCE_DOCUMENT == "VLD-INF-SINDRI-001"
    assert site_egress.SOURCE_REVISION
    assert "10.5" in site_egress.SOURCE_SECTION
    assert site_egress.TRANSCRIBED_ON


def test_the_transcription_holds_eighteen_hosts() -> None:
    """The estate register said seventeen. It is eighteen.

    Asserted because the count is the cheapest check on a re-transcription: a
    line dropped while copying eighteen hostnames out of a document is not
    something anyone notices by reading.
    """
    assert len(site_egress.SITE_ROUTER_POLICY) == 18
    assert len(set(site_egress.SITE_ROUTER_POLICY)) == 18, "the transcription repeats a host"


# ---------------------------------------------------------------------------
# The reconciliation
# ---------------------------------------------------------------------------


def test_every_host_the_programme_reaches_gets_a_verdict() -> None:
    """A host with no row is a host nobody has decided about."""
    rows = site_egress.reconcile()
    covered = {item.host for item in rows}

    assert covered >= set(egress.allow_listed_hosts())
    assert covered >= set(site_egress.SITE_ROUTER_POLICY)
    assert covered >= {item.host for item in site_egress.OTHER_CONSUMERS}


def test_the_divergence_matches_the_recorded_baseline() -> None:
    """The gate, and the reason it is a baseline rather than a target.

    Nine hosts are reached by this programme and refused by section 10.5, and
    none of them can be fixed from this repository. A test asserting "nothing
    is blocked" would fail from the day it was written and would be deleted or
    skipped within a week. This one fails on movement, which is the thing
    somebody can actually act on.
    """
    assert site_egress.unrecorded() == (), "a new divergence: see scripts/egress_policy.py"
    assert site_egress.resolved() == (), (
        "a baseline entry is no longer blocked, which means section 10.5 was amended "
        "and the transcription in site_egress.py is a revision behind"
    )


def test_the_estate_cannot_commission_itself_under_its_own_policy() -> None:
    """The sharpest part of the finding, asserted so it cannot quietly lapse.

    These three are reached by the manual's own procedures and refused by the
    manual's own section 10.5. Without them there is no uv, no PyTorch and no
    container toolkit, so Part 3 and Part 4 do not complete.
    """
    blocked = {item.host for item in site_egress.blocked()}

    assert {"astral.sh", "download.pytorch.org", "nvidia.github.io"} <= blocked


def test_the_control_plane_itself_reaches_nothing_the_router_refuses() -> None:
    """The one half of the divergence that is this repository's to own.

    Every blocked host belongs to the image build, commissioning or corpus
    acquisition. If a *control-plane* destination were ever blocked, the
    control plane would be making a call that silently times out in production,
    and that is a fix here rather than a change proposal.
    """
    at_run_time = [
        item for item in site_egress.blocked() if Consumer.CONTROL_PLANE in item.consumers
    ]

    assert at_run_time == [], (
        f"{[item.host for item in at_run_time]} are reached by the running control plane "
        "and dropped at the router, which presents as a timeout rather than a refusal"
    )


def test_a_new_blocked_host_is_reported_as_new() -> None:
    """The gate has to distinguish a fresh divergence from the old one."""
    invented = Requirement(
        host="telemetry.example.com",
        consumer=Consumer.CONTROL_PLANE,
        purpose="a dependency that started phoning home in a minor version bump",
        citation="hypothetical",
    )

    rows = site_egress.reconcile(others=(*site_egress.OTHER_CONSUMERS, invented))
    found = site_egress.unrecorded(rows)

    assert [item.host for item in found] == ["telemetry.example.com"]


def test_a_router_amendment_is_reported_as_a_stale_transcription() -> None:
    """The other direction, which is easy to forget and worse to miss.

    A baseline entry that stops being blocked does not mean somebody fixed it
    here. It means the document changed, and every other conclusion drawn from
    the transcription is a revision out of date.
    """
    amended = (*site_egress.SITE_ROUTER_POLICY, "astral.sh")

    rows = site_egress.reconcile(router_policy=amended)

    assert site_egress.resolved(rows) == ("astral.sh",)


def test_an_unclaimed_router_entry_is_reported_and_is_not_a_fault() -> None:
    """Docker Hub is permitted and the build uses three other registries.

    Not a failure: it may be a consumer nobody wrote down. But an entry nobody
    can justify is an entry nobody can remove, which is how an allow list grows
    in one direction only.
    """
    orphans = {item.host for item in site_egress.unclaimed()}

    assert "registry-1.docker.io" in orphans
    assert all(item.consumers == () for item in site_egress.unclaimed())


def test_internal_destinations_are_not_reported_as_blocked() -> None:
    """REGIN is not in section 10.5 and must not read as refused.

    This is the mistake the whole `traverses_site_router` field exists to
    prevent: three of the five control-plane destinations would otherwise
    appear in the blocked list, and the correct response to that report --
    adding them to the router's internet allow list -- would be wrong.
    """
    rows = {item.host: item for item in site_egress.reconcile()}

    assert rows["regin.sindri.veldris.internal"].verdict is Verdict.NOT_ROUTED
    assert rows["megingjord.veldris.internal"].verdict is Verdict.NOT_ROUTED


# ---------------------------------------------------------------------------
# AC-S3 still holds
# ---------------------------------------------------------------------------


def test_the_teacher_destination_is_absent_from_both_policies() -> None:
    """AC-S3. Widening the allow list is exactly when this gets added by accident."""
    assert egress.TEACHER_DESTINATION not in egress.allow_listed_hosts()
    assert egress.TEACHER_DESTINATION not in site_egress.SITE_ROUTER_POLICY
    assert not any("teacher" in item.host for item in site_egress.reconcile())


def test_the_generated_report_names_the_deliberate_absence() -> None:
    """Evidence has to record what is missing on purpose, or it reads as an oversight."""
    report = site_egress.to_markdown()

    assert egress.TEACHER_DESTINATION in report
    assert "AC-S3" in report
    assert "T3" in report


def test_the_report_states_what_the_pipeline_cannot_check() -> None:
    """The transcription is the weak link and the artefact should say so.

    A generated document that presented the reconciliation as fully automatic
    would be making the same claim this finding exists to correct, one level
    down.
    """
    report = site_egress.to_markdown()

    assert site_egress.TRANSCRIBED_ON in report
    assert "acceptance schedule" in report


def test_the_report_names_the_permission_whose_path_does_not_exist() -> None:
    """The gap field is only worth carrying if the artefact renders it.

    MEGINGJORD sits in the allow list alongside four working entries and reads
    exactly like them. The report is where that stops being true.
    """
    report = site_egress.to_markdown()

    assert "Declared, but not reachable yet" in report
    assert "megingjord.veldris.internal" in report
    assert "RF-E21" in report
