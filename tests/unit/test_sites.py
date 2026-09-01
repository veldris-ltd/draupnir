"""Site scope is chosen, never inferred.

The exit condition for Prompt 1 is that an unscoped query raises rather than
defaulting. That is asserted here at the domain boundary and again, against a
live PostgreSQL with row level security, in
`tests/integration/test_repositories.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from draupnir.core.domain.sites import (
    RELEASE_PERMITTING_STATES,
    AnchorState,
    ArtefactUriError,
    ReleaseBlockedError,
    Site,
    SiteRegistry,
    SiteScope,
    UnknownSiteError,
    UnscopedQueryError,
)

SINDRI = Site(
    id="sindri",
    name="Sindri",
    location="Nuneaton, United Kingdom",
    timezone="Europe/London",
    control_plane_uri="https://alviss.sindri.veldris.internal",
    anchor_state=AnchorState.ANCHORED,
    last_anchored_at=datetime(2026, 4, 11, tzinfo=UTC),
)
BROKKR = Site(
    id="brokkr",
    name="Brokkr",
    location="Nuneaton, United Kingdom",
    timezone="Europe/London",
    control_plane_uri="https://alviss.brokkr.veldris.internal",
    anchor_state=AnchorState.UNANCHORED,
)


@pytest.fixture
def registry() -> SiteRegistry:
    return SiteRegistry([SINDRI, BROKKR], local="sindri")


# ---------------------------------------------------------------------------
# Scope resolution
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("candidate", [None, "", "   "])
def test_an_absent_scope_raises_rather_than_defaulting(
    registry: SiteRegistry, candidate: str | None
) -> None:
    with pytest.raises(UnscopedQueryError) as raised:
        registry.scope(candidate, what="run list")
    assert "run list" in str(raised.value)
    # The message has to say why, or the next person adds a default.
    assert "no default" in str(raised.value)


def test_an_unknown_scope_names_what_was_known(registry: SiteRegistry) -> None:
    with pytest.raises(UnknownSiteError) as raised:
        registry.scope("eitri")
    assert raised.value.site_id == "eitri"
    assert raised.value.known == ("brokkr", "sindri")


def test_a_known_scope_resolves(registry: SiteRegistry) -> None:
    assert registry.scope("brokkr") == SiteScope("brokkr")
    assert registry.scope("  sindri  ").site_id == "sindri"


def test_the_local_scope_is_asked_for_explicitly(registry: SiteRegistry) -> None:
    assert registry.local_scope() == SiteScope("sindri")
    assert registry.local is SINDRI


def test_a_registry_cannot_name_a_local_site_it_does_not_hold() -> None:
    with pytest.raises(UnknownSiteError):
        SiteRegistry([SINDRI], local="brokkr")


def test_a_scope_renders_as_its_identifier() -> None:
    assert f"{SiteScope('sindri')}" == "sindri"


def test_the_registry_reports_what_it_holds(registry: SiteRegistry) -> None:
    assert registry.ids == ("brokkr", "sindri")
    assert len(registry) == 2
    assert "sindri" in registry
    assert "eitri" not in registry
    assert registry.get("brokkr") is BROKKR
    with pytest.raises(UnknownSiteError):
        registry.get("eitri")


# ---------------------------------------------------------------------------
# Anchor state
# ---------------------------------------------------------------------------


def test_only_an_anchored_site_may_release(registry: SiteRegistry) -> None:
    assert {AnchorState.ANCHORED} == RELEASE_PERMITTING_STATES
    registry.assert_may_release(SiteScope("sindri"))

    with pytest.raises(ReleaseBlockedError) as raised:
        registry.assert_may_release(SiteScope("brokkr"))
    assert raised.value.anchor_state is AnchorState.UNANCHORED


@pytest.mark.parametrize(
    "state", [AnchorState.UNANCHORED, AnchorState.PARTITIONED, AnchorState.DIVERGED]
)
def test_a_site_that_cannot_anchor_cannot_publish(state: AnchorState) -> None:
    # Decision S8: training continues through a partition; release does not.
    site = Site(
        id="brokkr",
        name="Brokkr",
        location="Nuneaton",
        timezone="Europe/London",
        control_plane_uri="https://example.internal",
        anchor_state=state,
    )
    assert not site.may_release
    with pytest.raises(ReleaseBlockedError):
        SiteRegistry([site], local="brokkr").assert_may_release(SiteScope("brokkr"))


def test_anchor_states_are_reported_per_site(registry: SiteRegistry) -> None:
    assert registry.anchor_states() == {
        "sindri": AnchorState.ANCHORED,
        "brokkr": AnchorState.UNANCHORED,
    }


# ---------------------------------------------------------------------------
# Artefact addressing, SAD 7.4
# ---------------------------------------------------------------------------


def test_an_authority_naming_a_site_is_site_scoped(registry: SiteRegistry) -> None:
    parsed = registry.resolve_uri("hodd://brokkr/models/core/MIDGARD-CORE-v1.0")
    assert parsed.site_id == "brokkr"
    assert parsed.path == "models/core/MIDGARD-CORE-v1.0"
    assert parsed.explicit


def test_an_authority_that_is_not_a_site_resolves_to_the_local_one(
    registry: SiteRegistry,
) -> None:
    # SAD 6.2 writes `hodd://models/core/...` while SAD 7.4 says the authority
    # carries the site. Treating the authority as a site only when it is one
    # keeps both spellings in the document valid.
    parsed = registry.resolve_uri("hodd://models/core/MIDGARD-CORE-v1.0")
    assert parsed.site_id == "sindri"
    assert parsed.path == "models/core/MIDGARD-CORE-v1.0"
    assert not parsed.explicit


def test_an_omitted_authority_resolves_to_the_local_site(registry: SiteRegistry) -> None:
    parsed = registry.resolve_uri("hodd:///corpora/GBR/curated")
    assert parsed.site_id == "sindri"
    assert parsed.path == "corpora/GBR/curated"


def test_a_resolved_uri_renders_fully_qualified(registry: SiteRegistry) -> None:
    parsed = registry.resolve_uri("hodd://models/core/X")
    assert str(parsed) == "hodd://sindri/models/core/X"


@pytest.mark.parametrize(
    "uri",
    ["s3://sindri/models/x", "hodd://sindri", "hodd://", "hodd:///"],
)
def test_an_unusable_address_is_refused(registry: SiteRegistry, uri: str) -> None:
    with pytest.raises(ArtefactUriError):
        registry.resolve_uri(uri)


# ---------------------------------------------------------------------------
# Residency, SAD 11C
# ---------------------------------------------------------------------------


def test_an_unconstrained_corpus_may_be_worked_anywhere(registry: SiteRegistry) -> None:
    assert registry.residency_permits(SiteScope("brokkr"), [])


def test_a_constrained_corpus_is_refused_at_a_site_not_named(registry: SiteRegistry) -> None:
    assert registry.residency_permits(SiteScope("sindri"), ["sindri"])
    assert not registry.residency_permits(SiteScope("brokkr"), ["sindri"])
