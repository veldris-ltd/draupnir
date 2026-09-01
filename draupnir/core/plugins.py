"""Entry point discovery, version negotiation and loading.

SAD 11B puts the entry point loader in the Ports layer: it knows interfaces,
never implementations. Nothing below imports a driver by name, and nothing in
this module knows that LLaMA-Factory or Slurm exist. SAD 11E.1 places the file
under `core/`, which is where it sits; the layering is a property of what it
imports, not of which directory it is in, and `.importlinter` holds it to that.

The four rules of SAD 10.3, and where each is enforced:

  1. Interfaces are versioned in the entry point name -- `InterfaceName.parse`,
     in `draupnir.interfaces.naming`.
  2. The core supports the current and immediately previous major version --
     `supported_majors`, checked in `_negotiate` below, which refuses anything
     else naming both what it found and what it expected.
  3. A run specification records the driver version used, and replaying
     resolves that version or fails loudly -- `PluginRegistry.resolve`, which
     never substitutes a different version for the one asked for.
  4. A plug-in declares its capabilities, and the core refuses to plan a job
     requiring one it has not declared -- `require_capabilities`, called before
     an allocation is consumed.

Discovery is fault tolerant and use is not. One malformed third party plug-in
must not stop the control plane starting, so a plug-in that fails to load is
recorded as a `LoadFailure` and reported; but asking for it afterwards raises,
with the reason it was refused. A control plane that boots while quietly
ignoring half its drivers is worse than one that refuses to boot.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from importlib.metadata import EntryPoint, entry_points
from typing import Any

import structlog

from draupnir.interfaces.naming import InterfaceName, InterfaceNameError, supported_majors
from draupnir.interfaces.protocols import CURRENT_MAJOR, PROTOCOL_FOR_GROUP
from draupnir.interfaces.signing import (
    SignatureStatus,
    SignatureVerifier,
    UnverifiedVerifier,
)
from draupnir.interfaces.types import GROUPS, RunSpec

logger = structlog.get_logger(__name__)

#: The one environment variable that lets an unverified plug-in load. Named
#: for what it enables rather than for a mode, so that finding it set in
#: production is unambiguous rather than arguable.
DEV_VARIABLE = "DRAUPNIR_DEV"


class PluginError(Exception):
    """Base class for every refusal this module makes."""


class UnsupportedVersionError(PluginError):
    """A plug-in declares a major version the core does not support."""

    def __init__(self, name: str, found: int, expected: Iterable[int]) -> None:
        """Name the version found and the versions that would have been taken."""
        self.plugin = name
        self.found = found
        self.expected = tuple(sorted(expected))
        super().__init__(
            f"{name} declares interface major version {found}, and this core supports "
            f"{', '.join(f'v{major}' for major in self.expected)}. "
            "A breaking change is a new major version, not an edit (SAD 10.3 rule 1); "
            "the core carries the current and the immediately previous one (rule 2)."
        )


class UnsignedPluginError(PluginError):
    """A plug-in is not signature verified and development mode is off."""

    def __init__(self, name: str, distribution: str, reason: str | None) -> None:
        """Name the plug-in, its distribution and why verification failed."""
        self.plugin = name
        self.distribution = distribution
        super().__init__(
            f"{name} (from {distribution}) is not signature verified: "
            f"{(reason or 'no reason given').rstrip('.')}. Plug-ins are verified at first load "
            f"(SAD 11.1 step 4). Set {DEV_VARIABLE}=1 to load unverified plug-ins "
            "during development."
        )


class ProtocolViolationError(PluginError):
    """A plug-in does not satisfy the Protocol its entry point group requires."""

    def __init__(self, name: str, group: str, missing: Iterable[str]) -> None:
        """Name what the group requires and what the object lacks."""
        self.plugin = name
        self.group = group
        self.missing = tuple(sorted(missing))
        super().__init__(
            f"{name} is registered under {group} but does not satisfy "
            f"{PROTOCOL_FOR_GROUP[group].__name__}: missing {', '.join(self.missing)}."
        )


class DriverNotFoundError(PluginError):
    """A specification names a driver that is not installed, or was refused."""

    def __init__(self, name: str, group: str, available: Iterable[str], reason: str = "") -> None:
        """Name what was asked for, what is available, and why if it was refused."""
        self.plugin = name
        self.group = group
        self.available = tuple(sorted(available))
        detail = f" It was refused at load: {reason}" if reason else ""
        offer = ", ".join(self.available) if self.available else "nothing"
        super().__init__(
            f"no driver {name!r} is loaded for {group}. Available: {offer}.{detail} "
            "Replaying a historical run resolves the version it recorded and fails "
            "loudly rather than substituting a newer one (SAD 10.3 rule 3)."
        )


class NoProviderError(PluginError):
    """Nothing installed declares the capabilities a specification asks for."""

    def __init__(
        self, group: str, required: Iterable[str], offered: Mapping[str, frozenset[str]]
    ) -> None:
        """Name what was asked for, and what each installed driver offers."""
        self.group = group
        self.required = frozenset(required)
        self.offered = dict(offered)
        catalogue = (
            "; ".join(
                f"{name} offers {', '.join(sorted(caps)) or 'nothing'}"
                for name, caps in sorted(self.offered.items())
            )
            or "nothing is installed"
        )
        demand = f"declares {', '.join(sorted(self.required))}" if self.required else "is installed"
        super().__init__(
            f"no {group} driver {demand}. {catalogue}. "
            "Adding one is an installation, not a core change (SAD 10.2, AC-N9)."
        )


class AmbiguousDriverError(PluginError):
    """More than one installed driver could satisfy the request."""

    def __init__(self, group: str, required: Iterable[str], candidates: Iterable[str]) -> None:
        """Name the candidates, so the operator can choose one."""
        self.group = group
        self.required = frozenset(required)
        self.candidates = tuple(sorted(candidates))
        super().__init__(
            f"{len(self.candidates)} {group} drivers declare "
            f"{', '.join(sorted(self.required))}: {', '.join(self.candidates)}. "
            "Name one in the specification rather than letting the core pick: a run "
            "must record the driver version it used (SAD 10.3 rule 3)."
        )


class MissingCapabilityError(PluginError):
    """A specification requires a capability the driver has not declared."""

    def __init__(
        self, name: str, group: str, required: Iterable[str], declared: Iterable[str]
    ) -> None:
        """Name the capabilities demanded, declared, and therefore missing."""
        self.plugin = name
        self.group = group
        self.required = frozenset(required)
        self.declared = frozenset(declared)
        self.missing = frozenset(self.required - self.declared)
        super().__init__(
            f"{name} does not declare {', '.join(sorted(self.missing))}, which this "
            f"specification requires of a {group} driver. It declares "
            f"{', '.join(sorted(self.declared)) or 'nothing'}. "
            "The core refuses to plan a job whose specification requires a capability "
            "the driver has not declared (SAD 10.3 rule 4)."
        )


@dataclass(frozen=True, slots=True)
class LoadedPlugin:
    """One plug-in that discovery accepted."""

    group: str
    name: InterfaceName
    distribution: str
    distribution_version: str
    driver: Any
    signature: SignatureStatus

    @property
    def capabilities(self) -> frozenset[str]:
        """What the driver declares it can do."""
        declared: frozenset[str] = getattr(self.driver, "capabilities", frozenset())
        return frozenset(declared)

    def __str__(self) -> str:
        """The entry point name, which is how everything else refers to it."""
        return str(self.name)


@dataclass(frozen=True, slots=True)
class LoadFailure:
    """One plug-in that discovery refused, and why.

    Recorded rather than raised, so that a single bad plug-in does not stop
    the control plane starting. Asking for it later raises.
    """

    group: str
    name: str
    distribution: str
    reason: str


def developer_mode(environ: Mapping[str, str] | None = None) -> bool:
    """Whether unverified plug-ins may load."""
    return (environ if environ is not None else os.environ).get(DEV_VARIABLE) == "1"


def _missing_protocol_members(driver: object, group: str) -> tuple[str, ...]:
    """Return the members `group`'s Protocol requires that `driver` lacks.

    `isinstance` against a runtime-checkable Protocol answers yes or no;
    a plug-in author needs to know which member is missing.
    """
    protocol = PROTOCOL_FOR_GROUP[group]
    required = getattr(protocol, "__protocol_attrs__", None) or {
        member
        for klass in protocol.__mro__
        if klass.__module__.startswith("draupnir.")
        for member in {**getattr(klass, "__annotations__", {}), **vars(klass)}
        if not member.startswith("_")
    }
    return tuple(sorted(member for member in required if not hasattr(driver, member)))


class PluginRegistry:
    """Every plug-in this core will use, and every one it refused."""

    def __init__(
        self, loaded: Sequence[LoadedPlugin] = (), failures: Sequence[LoadFailure] = ()
    ) -> None:
        """Build a registry from already-resolved plug-ins."""
        self._loaded: dict[str, dict[str, LoadedPlugin]] = {}
        for plugin in loaded:
            self._loaded.setdefault(plugin.group, {})[str(plugin.name)] = plugin
        self._failures = tuple(failures)

    # -- discovery ---------------------------------------------------------

    @classmethod
    def discover(
        cls,
        *,
        groups: Sequence[str] = GROUPS,
        verifier: SignatureVerifier | None = None,
        environ: Mapping[str, str] | None = None,
        points: Iterable[EntryPoint] | None = None,
    ) -> PluginRegistry:
        """Find, verify and load every installed plug-in.

        `points` exists so that a test can present entry points without
        installing distributions; production passes nothing and the installed
        environment is read.
        """
        verifier = verifier or UnverifiedVerifier()
        allow_unsigned = developer_mode(environ)

        loaded: list[LoadedPlugin] = []
        failures: list[LoadFailure] = []

        for group in groups:
            for point in cls._entry_points(group, points):
                try:
                    loaded.append(cls._load(group, point, verifier, allow_unsigned=allow_unsigned))
                except PluginError as refusal:
                    failures.append(
                        LoadFailure(
                            group=group,
                            name=point.name,
                            distribution=_distribution_of(point),
                            reason=str(refusal),
                        )
                    )
                    logger.error(
                        "plugin.refused",
                        group=group,
                        plugin=point.name,
                        distribution=_distribution_of(point),
                        reason=str(refusal),
                    )
                except Exception as error:
                    failures.append(
                        LoadFailure(
                            group=group,
                            name=point.name,
                            distribution=_distribution_of(point),
                            reason=f"{type(error).__name__}: {error}",
                        )
                    )
                    logger.error(
                        "plugin.load_failed",
                        group=group,
                        plugin=point.name,
                        error=str(error),
                    )

        return cls(loaded, failures)

    @staticmethod
    def _entry_points(group: str, points: Iterable[EntryPoint] | None) -> list[EntryPoint]:
        if points is None:
            return list(entry_points(group=group))
        return [point for point in points if point.group == group]

    @classmethod
    def _load(
        cls,
        group: str,
        point: EntryPoint,
        verifier: SignatureVerifier,
        *,
        allow_unsigned: bool,
    ) -> LoadedPlugin:
        name = cls._negotiate(group, point.name)
        distribution, version = _distribution_of(point), _version_of(point)

        signature = verifier.verify(distribution, version)
        if not signature.verified:
            if not allow_unsigned:
                raise UnsignedPluginError(point.name, distribution, signature.reason)
            logger.warning(
                "plugin.unverified",
                plugin=point.name,
                group=group,
                distribution=distribution,
                reason=signature.reason,
                detail=(
                    f"loaded because {DEV_VARIABLE}=1. Signature verification is a "
                    "control, not a convention (SAD 9.3)."
                ),
            )

        driver = point.load()
        driver = driver() if isinstance(driver, type) else driver

        missing = _missing_protocol_members(driver, group)
        if missing:
            raise ProtocolViolationError(point.name, group, missing)

        declared_name = getattr(driver, "name", None)
        if declared_name != point.name:
            msg = (
                f"{point.name} is registered under that entry point name but declares "
                f"name={declared_name!r}. The two must agree, or a specification naming "
                "one will resolve the other."
            )
            raise ProtocolViolationError(point.name, group, (msg,))

        logger.info(
            "plugin.loaded",
            group=group,
            plugin=point.name,
            distribution=distribution,
            verified=signature.verified,
            capabilities=sorted(frozenset(getattr(driver, "capabilities", frozenset()))),
        )
        return LoadedPlugin(
            group=group,
            name=name,
            distribution=distribution,
            distribution_version=version,
            driver=driver,
            signature=signature,
        )

    @staticmethod
    def _negotiate(group: str, raw: str) -> InterfaceName:
        """Parse the entry point name and check its major version. SAD 10.3."""
        try:
            name = InterfaceName.parse(raw)
        except InterfaceNameError as error:
            raise ProtocolViolationError(raw, group, (str(error),)) from error

        supported = supported_majors(CURRENT_MAJOR[group])
        if name.major not in supported:
            raise UnsupportedVersionError(raw, name.major, supported)
        return name

    # -- use ---------------------------------------------------------------

    @property
    def failures(self) -> tuple[LoadFailure, ...]:
        """Every plug-in discovery refused, with the reason."""
        return self._failures

    def names(self, group: str) -> tuple[str, ...]:
        """Every loaded plug-in name in `group`, sorted."""
        return tuple(sorted(self._loaded.get(group, {})))

    def all(self, group: str) -> tuple[LoadedPlugin, ...]:
        """Every loaded plug-in in `group`, in name order."""
        return tuple(self._loaded.get(group, {})[name] for name in self.names(group))

    def resolve(self, group: str, name: str) -> LoadedPlugin:
        """Return the named driver, or raise naming what is available.

        SAD 10.3 rule 3: the version asked for is the version returned. There
        is no nearest match and no upgrade.
        """
        found = self._loaded.get(group, {}).get(name)
        if found is not None:
            return found
        refused = next((failure for failure in self._failures if failure.name == name), None)
        raise DriverNotFoundError(name, group, self.names(group), refused.reason if refused else "")

    def require_capabilities(self, plugin: LoadedPlugin, required: Iterable[str]) -> None:
        """Raise unless the driver declares everything `required` asks for."""
        wanted = frozenset(required)
        if not wanted <= plugin.capabilities:
            raise MissingCapabilityError(
                str(plugin.name), plugin.group, wanted, plugin.capabilities
            )

    def providers(self, group: str, required: Iterable[str]) -> tuple[LoadedPlugin, ...]:
        """Every loaded driver in `group` declaring all of `required`."""
        wanted = frozenset(required)
        return tuple(plugin for plugin in self.all(group) if wanted <= plugin.capabilities)

    def provider(self, group: str, required: Iterable[str]) -> LoadedPlugin:
        """Return the one driver that can do this, or say why there is not one.

        Some groups are chosen by capability rather than by name. A run
        specification asks for formats -- `nvfp4`, `gguf-q4km` -- and never
        names an export driver, so the core finds one that declares them. That
        is what makes AC-N9 possible: a new format is a new distribution, and
        no table in the core lists which driver produces what.
        """
        wanted = frozenset(required)
        candidates = self.providers(group, wanted)
        if not candidates:
            raise NoProviderError(
                group, wanted, {str(p.name): p.capabilities for p in self.all(group)}
            )
        if len(candidates) > 1:
            raise AmbiguousDriverError(group, wanted, (str(p.name) for p in candidates))
        return candidates[0]

    def for_spec(self, spec: RunSpec, group: str) -> LoadedPlugin:
        """Resolve the driver a specification names, and check its capabilities.

        This is the call the planner makes, and it is made *before* an
        allocation is consumed: refusing afterwards would have spent the
        scarcest resource on this estate to learn something knowable in advance
        (SAD 11D).
        """
        named = spec.driver_for(group)
        if named is None:
            # The specification names no driver for this group, so the core
            # selects one by what it declares. SAD 6.2's `release` block asks
            # for formats and never for an exporter.
            return self.provider(group, spec.capabilities_for(group))

        plugin = self.resolve(group, named)
        self.require_capabilities(plugin, spec.capabilities_for(group))
        return plugin


def _distribution_of(point: EntryPoint) -> str:
    distribution = getattr(point, "dist", None)
    return getattr(distribution, "name", None) or "<unknown distribution>"


def _version_of(point: EntryPoint) -> str:
    distribution = getattr(point, "dist", None)
    return getattr(distribution, "version", None) or "0"


@dataclass(frozen=True, slots=True)
class PluginReport:
    """A summary of what loaded and what did not, for `readyz` and the console."""

    loaded: tuple[str, ...] = field(default_factory=tuple)
    refused: tuple[str, ...] = field(default_factory=tuple)
    unverified: tuple[str, ...] = field(default_factory=tuple)

    @property
    def healthy(self) -> bool:
        """Whether every installed plug-in loaded and verified."""
        return not self.refused and not self.unverified


def report(registry: PluginRegistry, *, groups: Sequence[str] = GROUPS) -> PluginReport:
    """Summarise a registry, so that an operator can see the whole picture."""
    loaded = tuple(str(plugin.name) for group in groups for plugin in registry.all(group))
    unverified = tuple(
        str(plugin.name)
        for group in groups
        for plugin in registry.all(group)
        if not plugin.signature.verified
    )
    refused = tuple(f"{failure.name} ({failure.group})" for failure in registry.failures)
    return PluginReport(loaded=loaded, refused=refused, unverified=unverified)
