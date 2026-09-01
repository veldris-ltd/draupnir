"""Sites: the forge registry, site scope resolution and anchor state.

SAD 11A.2, Decision S12: a **site** is one forge, with its own control plane,
ledger segment and scheduler. A **node** is one appliance within a forge. The
two words are not interchangeable, and nothing in this module uses the second
for the first.

The central rule here is that a scope is never inferred. SAD 7.4 lets an
artefact URI omit its authority and mean "the local site", which is a
convenience for a human writing a run specification; it is not licence for a
query to default. `SiteScope.resolve` raises on an absent scope rather than
picking one, because a query that quietly reads site 1 is indistinguishable
from a query that meant to.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from urllib.parse import urlsplit

#: The scheme HODD resolves. SAD 7.4.
ARTEFACT_SCHEME = "hodd"


class AnchorState(StrEnum):
    """Where a site stands with the federation. SAD 11A.3 and 11A.4."""

    #: No head of this site's chain has been countersigned yet.
    UNANCHORED = "UNANCHORED"
    #: A head has been countersigned and the link is up.
    ANCHORED = "ANCHORED"
    #: The link is down. Training continues; release does not (Decision S8).
    PARTITIONED = "PARTITIONED"
    #: The chain disagrees with an anchor. The forge is read only until
    #: investigated, and no release may leave it (SAD 11A.4).
    DIVERGED = "DIVERGED"


#: The states in which this site may publish a release. A partitioned forge
#: keeps training and keeps writing its own ledger; it cannot release, because
#: publication needs the federation trust root and a countersigned anchor.
RELEASE_PERMITTING_STATES: frozenset[AnchorState] = frozenset({AnchorState.ANCHORED})


class UnscopedQueryError(Exception):
    """Raised when work is attempted without a site scope.

    Deliberately not a subclass of anything that a broad `except` would catch
    by accident: an unscoped query is a programming error, not a condition to
    be handled.
    """

    def __init__(self, what: str = "query") -> None:
        """Name what was attempted unscoped."""
        self.what = what
        super().__init__(
            f"{what} attempted without a site scope. A forge is a site; "
            "there is no default (SAD 11A.2, Decision S12)."
        )


class UnknownSiteError(Exception):
    """Raised when a scope names a site the registry does not hold."""

    def __init__(self, site_id: str, known: Iterable[str]) -> None:
        """Record the unknown site and what was known."""
        self.site_id = site_id
        self.known = tuple(sorted(known))
        super().__init__(f"unknown site {site_id!r}; the registry holds {', '.join(self.known)}")


class ReleaseBlockedError(Exception):
    """Raised when a release is attempted from a site that may not publish."""

    def __init__(self, site_id: str, anchor_state: AnchorState) -> None:
        """Record the site and why it may not release."""
        self.site_id = site_id
        self.anchor_state = anchor_state
        super().__init__(
            f"site {site_id!r} is {anchor_state} and may not publish a release "
            "(SAD 11A.4, Decision S8)"
        )


@dataclass(frozen=True, slots=True)
class Site:
    """One forge. Attributes per SAD 7.1."""

    id: str
    name: str
    location: str
    timezone: str
    control_plane_uri: str
    anchor_state: AnchorState = AnchorState.UNANCHORED
    last_anchored_at: datetime | None = None

    @property
    def may_release(self) -> bool:
        """Whether this site's anchor state permits publication."""
        return self.anchor_state in RELEASE_PERMITTING_STATES


@dataclass(frozen=True, slots=True)
class SiteScope:
    """A resolved site scope. Holding one is the proof a scope was chosen."""

    site_id: str

    def __str__(self) -> str:
        """The site identifier, for interpolation into a log line."""
        return self.site_id

    @classmethod
    def resolve(
        cls, candidate: str | None, *, known: Iterable[str], what: str = "query"
    ) -> SiteScope:
        """Return a scope for `candidate`, or raise.

        Raises `UnscopedQueryError` when nothing was supplied and
        `UnknownSiteError` when what was supplied is not a registered site.
        Neither case falls back to a default.
        """
        if candidate is None or not candidate.strip():
            raise UnscopedQueryError(what)
        site_id = candidate.strip()
        registered = tuple(known)
        if site_id not in registered:
            raise UnknownSiteError(site_id, registered)
        return cls(site_id)


@dataclass(frozen=True, slots=True)
class ArtefactUri:
    """A parsed `hodd://` artefact address. SAD 7.4."""

    site_id: str
    path: str
    #: True when the authority named a site explicitly, false when the address
    #: was relative and resolved to the local one.
    explicit: bool

    def __str__(self) -> str:
        """Render the fully qualified form, which always names its site."""
        return f"{ARTEFACT_SCHEME}://{self.site_id}/{self.path.lstrip('/')}"


class ArtefactUriError(ValueError):
    """Raised when an artefact URI cannot be parsed."""


class SiteRegistry:
    """The forge registry, and the resolver that scopes work to one forge.

    Pure: it holds sites and answers questions about them. Loading it from the
    `site` table is the repository's job.
    """

    def __init__(self, sites: Iterable[Site], *, local: str) -> None:
        """Build a registry, naming which site this control plane serves."""
        self._sites: dict[str, Site] = {site.id: site for site in sites}
        if local not in self._sites:
            raise UnknownSiteError(local, self._sites)
        self._local = local

    @property
    def local(self) -> Site:
        """The site this control plane serves."""
        return self._sites[self._local]

    @property
    def ids(self) -> tuple[str, ...]:
        """Every registered site identifier, sorted."""
        return tuple(sorted(self._sites))

    def __contains__(self, site_id: object) -> bool:
        """Whether `site_id` is registered."""
        return site_id in self._sites

    def __len__(self) -> int:
        """How many forges the registry holds."""
        return len(self._sites)

    def get(self, site_id: str) -> Site:
        """Return a site, or raise `UnknownSiteError`."""
        try:
            return self._sites[site_id]
        except KeyError as error:
            raise UnknownSiteError(site_id, self._sites) from error

    def scope(self, candidate: str | None, *, what: str = "query") -> SiteScope:
        """Resolve an explicit scope. Never falls back to the local site."""
        return SiteScope.resolve(candidate, known=self._sites, what=what)

    def local_scope(self) -> SiteScope:
        """Return the local site's scope.

        Spelled out as its own call so that reading local data is a decision
        somebody wrote down, rather than what happens when an argument is None.
        """
        return SiteScope(self._local)

    def assert_may_release(self, scope: SiteScope) -> None:
        """Raise unless the scoped site's anchor state permits publication."""
        site = self.get(scope.site_id)
        if not site.may_release:
            raise ReleaseBlockedError(site.id, site.anchor_state)

    def resolve_uri(self, uri: str) -> ArtefactUri:
        """Parse a `hodd://` address, resolving a relative one to the local site.

        SAD 6.2 writes `hodd://models/core/...` while SAD 7.4 says the
        authority carries the site. The two are reconciled by treating the
        authority as a site only when it is a registered site identifier;
        anything else is the head of the path at the local site. That keeps
        both spellings in the document valid and, more usefully, means a run
        specification written before a second forge existed keeps resolving
        after one is registered.
        """
        parts = urlsplit(uri)
        if parts.scheme != ARTEFACT_SCHEME:
            msg = f"{uri!r} is not a {ARTEFACT_SCHEME}:// address"
            raise ArtefactUriError(msg)

        authority, path = parts.netloc, parts.path.lstrip("/")
        if authority and authority in self._sites:
            if not path:
                msg = f"{uri!r} names a site but no artefact"
                raise ArtefactUriError(msg)
            return ArtefactUri(site_id=authority, path=path, explicit=True)

        relative = f"{authority}/{path}".strip("/") if authority else path
        if not relative:
            msg = f"{uri!r} names no artefact"
            raise ArtefactUriError(msg)
        return ArtefactUri(site_id=self._local, path=relative, explicit=False)

    def residency_permits(self, scope: SiteScope, constraint: Iterable[str]) -> bool:
        """Whether a residency constrained corpus may be worked on at `scope`.

        SAD 11C: an empty constraint means unconstrained. The check belongs at
        planning rather than execution, which is why it is a question the
        registry answers rather than something the executor discovers.
        """
        permitted = tuple(constraint)
        return not permitted or scope.site_id in permitted

    def anchor_states(self) -> Mapping[str, AnchorState]:
        """Every site's anchor state, for the federation view of the console."""
        return {site.id: site.anchor_state for site in self._sites.values()}
