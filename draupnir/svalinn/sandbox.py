"""The executor sandbox profile: rootless, no network, read-only artefacts.

Threat T7 mitigates a malicious or compromised plug-in by executing it "in a
rootless container with no outbound network and a read only artefact mount".
Threat T11 adds that "executors run with no outbound network at all". AC-S11
tests it: an executor attempting an outbound connection fails, and the attempt
appears in the log.

The profile is data rather than a shell command for two reasons. It has to be
rendered for more than one runtime -- systemd-nspawn on the appliances, Docker
for local development, and whatever the second forge chooses -- and it has to
be *asserted about*. A profile expressed as a string of flags can be tested for
the presence of `--network=none`; a profile expressed as fields can be tested
for the absence of any way to turn it on.

The strictness is deliberate and is not configurable per run. Every field that
weakens the sandbox is absent rather than defaulted: there is no
`allow_network`, no `privileged`, no `writable_artefacts`. A profile with such
a field is a profile that will be relaxed for one job that needed it, and the
relaxation will outlive the job.

The egress broker (`egress`) governs the control plane, which does make
outbound calls. This governs the executor, which makes none. Two layers,
because the sandbox is the one an escaping dependency cannot argue with and the
broker is the one that produces a record.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final

#: The unprotected user an executor runs as. Matches the distroless nonroot
#: uid the images already use, so the sandbox and the image agree.
NONROOT_UID: Final = 65532
NONROOT_GID: Final = 65532

#: Linux capabilities an executor keeps. None: a training job needs no
#: capability at all, and the empty set is easier to defend than a short one.
CAPABILITIES: Final[frozenset[str]] = frozenset()


class Runtime(StrEnum):
    """Where a profile is rendered for."""

    SYSTEMD_NSPAWN = "systemd-nspawn"
    DOCKER = "docker"
    PODMAN = "podman"


class SandboxError(Exception):
    """Raised when a sandbox profile cannot be built or is unsafe."""


@dataclass(frozen=True, slots=True)
class Mount:
    """One path visible inside the sandbox."""

    source: str
    target: str
    #: Artefact mounts are read-only. The one writable mount is the working
    #: directory, and it is created per job rather than shared.
    read_only: bool = True

    def as_payload(self) -> dict[str, Any]:
        """The wire shape."""
        return {"source": self.source, "target": self.target, "readOnly": self.read_only}


@dataclass(frozen=True, slots=True)
class SandboxProfile:
    """How an executor runs. Every weakening is an absence, not a default.

    Note what this class does not have: no `network`, no `privileged`, no
    `user`, no `capabilities` to add to. Those are properties below, computed
    from constants, so there is no argument anywhere that turns them on.
    """

    #: Where the job writes. The only writable mount.
    workdir: str
    #: Artefacts the job reads. All read-only, checked on construction.
    artefacts: tuple[Mount, ...] = ()
    #: Lease references, never values (see `secrets.brokered_environment`).
    environment: Mapping[str, str] = field(default_factory=dict)
    #: Bytes. A job that exceeds it is killed rather than swapping the host.
    memory_limit: int | None = None

    def __post_init__(self) -> None:
        """Refuse a profile that is not what it claims to be."""
        writable = [item.target for item in self.artefacts if not item.read_only]
        if writable:
            msg = (
                f"artefact mount(s) {', '.join(writable)} are writable. Artefacts mount "
                "read only (threat T7): a job that can write to the artefact store can "
                "modify a checkpoint after it was gated, which is threat T8 reached "
                "from inside."
            )
            raise SandboxError(msg)

        leaked = [
            key
            for key, value in self.environment.items()
            if not key.startswith("DRAUPNIR_LEASE_") and _looks_like_a_secret(key, value)
        ]
        if leaked:
            msg = (
                f"the executor environment carries {', '.join(sorted(leaked))}, which "
                "looks like a credential rather than a lease reference. Secrets are "
                "brokered and redeemed by the job at start; they are never written "
                "into a job environment (threat T6)."
            )
            raise SandboxError(msg)

    # -- the properties that are not arguments ----------------------------

    @property
    def uid(self) -> int:
        """Rootless. Not configurable."""
        return NONROOT_UID

    @property
    def gid(self) -> int:
        """Rootless. Not configurable."""
        return NONROOT_GID

    @property
    def network(self) -> str:
        """No outbound network namespace. Not configurable. AC-S11."""
        return "none"

    @property
    def capabilities(self) -> frozenset[str]:
        """No Linux capabilities. Not configurable."""
        return CAPABILITIES

    @property
    def no_new_privileges(self) -> bool:
        """A process inside cannot gain privileges. Not configurable."""
        return True

    @property
    def read_only_root(self) -> bool:
        """The root filesystem is read-only. Not configurable."""
        return True

    def as_payload(self) -> dict[str, Any]:
        """The profile, for the ledger entry that records how a job ran."""
        return {
            "uid": self.uid,
            "gid": self.gid,
            "network": self.network,
            "capabilities": sorted(self.capabilities),
            "noNewPrivileges": self.no_new_privileges,
            "readOnlyRoot": self.read_only_root,
            "workdir": self.workdir,
            "memoryLimit": self.memory_limit,
            "mounts": [item.as_payload() for item in self.artefacts],
            "environmentKeys": sorted(self.environment),
        }

    def render(self, runtime: Runtime | str) -> tuple[str, ...]:
        """The runtime's arguments for this profile.

        Rendered rather than stored, so that a new runtime is a branch here
        and not a second place the security properties are written down.
        """
        chosen = Runtime(runtime)
        if chosen in {Runtime.DOCKER, Runtime.PODMAN}:
            arguments = [
                "--network=none",
                f"--user={self.uid}:{self.gid}",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--read-only",
                f"--workdir={self.workdir}",
                "--tmpfs=/tmp:rw,noexec,nosuid",
            ]
            if self.memory_limit:
                arguments.append(f"--memory={self.memory_limit}")
            arguments += [
                f"--volume={item.source}:{item.target}:{'ro' if item.read_only else 'rw'}"
                for item in self.artefacts
            ]
            return tuple(arguments)

        arguments = [
            "--private-network",
            f"--private-users={self.uid}",
            "--capability=",
            "--read-only",
            f"--chdir={self.workdir}",
        ]
        arguments += [
            f"--bind-ro={item.source}:{item.target}"
            if item.read_only
            else f"--bind={item.source}:{item.target}"
            for item in self.artefacts
        ]
        return tuple(arguments)


def _looks_like_a_secret(key: str, value: str) -> bool:
    """Whether an environment entry looks like a credential rather than a name.

    Deliberately crude. This is a tripwire on the way into the sandbox, not the
    scanner; `scanning` is the thorough one and runs over what comes out.
    """
    suspicious = ("token", "secret", "password", "passwd", "api_key", "apikey", "credential")
    named = any(word in key.lower() for word in suspicious)
    return named and len(value) >= 8


def for_job(
    *,
    workdir: str,
    artefacts: Iterable[tuple[str, str]] = (),
    environment: Mapping[str, str] | None = None,
    memory_limit: int | None = None,
) -> SandboxProfile:
    """Build the profile for one job. Artefact mounts are read-only by force."""
    return SandboxProfile(
        workdir=workdir,
        artefacts=tuple(Mount(source=source, target=target) for source, target in artefacts),
        environment=dict(environment or {}),
        memory_limit=memory_limit,
    )


def violations(profile: SandboxProfile) -> tuple[str, ...]:
    """Every way a profile falls short. Empty for anything this module builds.

    Exists so that a profile arriving from elsewhere -- a runbook, a
    configuration file, a future site with its own runtime -- can be held to
    the same statement rather than trusted.
    """
    found: list[str] = []
    if profile.network != "none":
        found.append("the executor has an outbound network namespace (threat T11)")
    if profile.uid == 0:
        found.append("the executor runs as root (threat T7)")
    if profile.capabilities:
        found.append(f"the executor keeps capabilities: {sorted(profile.capabilities)}")
    if not profile.no_new_privileges:
        found.append("a process inside can gain privileges")
    if not profile.read_only_root:
        found.append("the root filesystem is writable")
    found += [
        f"artefact mount {item.target} is writable (threat T8 from inside)"
        for item in profile.artefacts
        if not item.read_only
    ]
    return tuple(found)


@dataclass(frozen=True, slots=True)
class BlockedConnection:
    """One outbound attempt the sandbox refused. AC-S11's log line."""

    run_id: str
    destination: str
    at: str

    def as_log_context(self) -> dict[str, Any]:
        """The structured log line."""
        return {
            "event": "egress.blocked",
            "layer": "executor-sandbox",
            "runId": self.run_id,
            "destination": self.destination,
            "at": self.at,
            "reason": "the executor has no outbound network namespace (threat T11)",
        }


def blocked(attempts: Sequence[BlockedConnection]) -> tuple[dict[str, Any], ...]:
    """Render blocked attempts for the log. AC-S11 requires them to appear."""
    return tuple(item.as_log_context() for item in attempts)
