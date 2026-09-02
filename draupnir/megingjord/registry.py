"""The site registry, policy distribution, and the signing trust root.

SAD 11A.1 gives MEGINGJORD five jobs: the global CIM-56 model registry, cross
forge ledger anchors, policy and gate distribution, the OIDC issuer and RBAC
source of truth, and the plug-in signature trust root with the internal PKI and
self-hosted transparency log. Anchoring is in `anchors`; the rest is here.

Everything crosses the boundary through `payloads.sealed`, so the constraint
that MEGINGJORD holds hashes and policy rather than corpora or weights is a
property of the code path rather than of the schema.

Policy distribution is pull, not push. A forge asks for the policy at a version
and receives it signed; MEGINGJORD never reaches into a site. That keeps the
partition behaviour of SAD 11A.4 simple -- a forge that cannot reach the
registry keeps the policy it last pulled and carries on -- and it means a
compromised registry cannot change what a disconnected forge is doing.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from draupnir.core.domain.federation import (
    Capacity,
    PolicyBundle,
    ReleaseRecord,
    Site,
    sealed,
)


class RegistryError(Exception):
    """Raised when a site or a policy cannot be resolved."""


@dataclass
class FederationRegistry:
    """The Matrix tier: sites, policy, release metadata, capacity, trust root."""

    sites: dict[str, Site] = field(default_factory=dict)
    policies: dict[str, PolicyBundle] = field(default_factory=dict)
    releases: list[ReleaseRecord] = field(default_factory=list)
    capacity: dict[str, Capacity] = field(default_factory=dict)
    #: Plug-in signing keys the federation trusts. SAD 11A.1's trust root.
    plugin_trust_root: dict[str, str] = field(default_factory=dict)

    # -- sites -------------------------------------------------------------

    def register_site(self, site: Site) -> Site:
        """Add a forge. Re-registering with a different key is refused.

        A site's signing key changing is either a key rotation, which is an
        operation with a procedure, or somebody else claiming to be that site.
        Neither should be a silent update.
        """
        existing = self.sites.get(site.site_id)
        if existing is not None and existing.signing_key_id != site.signing_key_id:
            msg = (
                f"site {site.site_id} is registered with signing key "
                f"{existing.signing_key_id!r} and is being re-registered with "
                f"{site.signing_key_id!r}. A key change is a rotation with a procedure, "
                "or it is somebody else claiming to be this site."
            )
            raise RegistryError(msg)
        self.sites[site.site_id] = site
        return site

    def site(self, site_id: str) -> Site:
        """One site, or raise."""
        try:
            return self.sites[site_id]
        except KeyError as error:
            known = ", ".join(sorted(self.sites))
            msg = f"no site {site_id!r} is registered; known: {known or 'none'}"
            raise RegistryError(msg) from error

    # -- policy ------------------------------------------------------------

    def publish_policy(self, bundle: PolicyBundle) -> PolicyBundle:
        """Publish a policy version. A published version is immutable.

        A policy version whose digest changed is a different policy wearing the
        same version, and every decision recorded against that version becomes
        unexplainable.
        """
        existing = self.policies.get(bundle.key)
        if existing is not None and existing.digest != bundle.digest:
            msg = (
                f"policy {bundle.key} is published with digest {existing.digest[:12]} "
                f"and is being republished as {bundle.digest[:12]}. A published version "
                "is immutable: every decision recorded against it would otherwise "
                "become unexplainable. Issue a new version."
            )
            raise RegistryError(msg)
        self.policies[bundle.key] = bundle
        return bundle

    def pull_policy(self, name: str, version: str | None = None) -> PolicyBundle:
        """A forge pulls a policy. Pull, never push. SAD 11A.4.

        With no version, the most recently issued. A forge that cannot reach
        the registry keeps what it last pulled and carries on; a compromised
        registry cannot change what a disconnected forge is doing.
        """
        if version is not None:
            try:
                return self.policies[f"{name}/{version}"]
            except KeyError as error:
                msg = f"no policy {name}/{version} is published"
                raise RegistryError(msg) from error

        candidates = [item for item in self.policies.values() if item.name == name]
        if not candidates:
            known = ", ".join(sorted({item.name for item in self.policies.values()}))
            msg = f"no policy named {name!r} is published; known: {known or 'none'}"
            raise RegistryError(msg)
        return max(candidates, key=lambda item: item.issued_at)

    # -- release metadata and capacity -------------------------------------

    def record_release(self, record: ReleaseRecord) -> ReleaseRecord:
        """Record release metadata pushed by a site."""
        self.site(record.site_id)
        self.releases.append(record)
        return record

    def report_capacity(self, report: Capacity) -> Capacity:
        """Record a site's capacity report."""
        self.site(report.site_id)
        self.capacity[report.site_id] = report
        return report

    def releases_for(self, site_id: str) -> tuple[ReleaseRecord, ...]:
        """Every release a site has recorded."""
        return tuple(item for item in self.releases if item.site_id == site_id)

    # -- trust root --------------------------------------------------------

    def trust_plugin_key(self, key_id: str, public_key_hex: str) -> None:
        """Add a plug-in signing key to the federation trust root."""
        self.plugin_trust_root[key_id] = public_key_hex

    def as_payload(self) -> Mapping[str, Any]:
        """Everything the registry holds, sealed. Hashes, names and numbers."""
        return sealed(
            {
                "sites": [item.as_payload() for _, item in sorted(self.sites.items())],
                "policies": [item.as_payload() for _, item in sorted(self.policies.items())],
                "releases": [item.as_payload() for item in self.releases],
                "capacity": [item.as_payload() for _, item in sorted(self.capacity.items())],
                "pluginTrustRoot": sorted(self.plugin_trust_root),
            },
            name="registry",
        )


def sindri(registered_at: datetime, signing_key_id: str = "sindri-anchor-1") -> Site:
    """Site 0. The first and currently only member of the Forge Matrix."""
    return Site(
        site_id="sindri",
        ordinal=0,
        fqdn="sindri.veldris.internal",
        signing_key_id=signing_key_id,
        registered_at=registered_at,
        permitted_residency=("GBR",),
    )


def payloads_of(registry: FederationRegistry) -> tuple[Mapping[str, Any], ...]:
    """Every payload the registry would put on the wire. For AC-S14."""
    return (
        *(item.as_payload() for item in registry.sites.values()),
        *(item.as_payload() for item in registry.policies.values()),
        *(item.as_payload() for item in registry.releases),
        *(item.as_payload() for item in registry.capacity.values()),
    )


def active_sites(registry: FederationRegistry) -> tuple[Site, ...]:
    """Every site currently participating."""
    return tuple(
        sorted((item for item in registry.sites.values() if item.active), key=lambda s: s.ordinal)
    )


def residency_permitted(registry: FederationRegistry, jurisdiction: str) -> tuple[str, ...]:
    """Which sites may hold a residency-constrained corpus. SAD 11C."""
    return tuple(
        sorted(
            item.site_id
            for item in registry.sites.values()
            if jurisdiction in item.permitted_residency
        )
    )


def known_policies(registry: FederationRegistry) -> tuple[str, ...]:
    """Every published policy, as `name/version`."""
    return tuple(sorted(registry.policies))


def trusted_keys(registry: FederationRegistry) -> tuple[str, ...]:
    """Every plug-in signing key the federation trusts."""
    return tuple(sorted(registry.plugin_trust_root))


def iter_sites(registry: FederationRegistry) -> Iterable[Site]:
    """Every registered site."""
    return registry.sites.values()
