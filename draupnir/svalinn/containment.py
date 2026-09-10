"""How a training job is actually contained at a forge.

As opposed to how a container-per-job estate would contain one, which is what
`sandbox` describes and what the documents claimed until RF-E16.

`sandbox` describes a rootless container with no network namespace, a read-only
root and read-only artefact mounts. It is a good design and it is **not in
force at Sindri**, because Sindri does not run jobs in containers. Procedure M6
runs `python /forge/tools/LLaMA-Factory/src/train.py` inside `/forge/venv`
under `slurmd`, and that is a process on a shared host.

The two are not reconcilable by adjusting the profile, because the work as
specified requires the two properties the profile forbids: the job reports to
MLflow on ANDVARI, which needs a network, and it writes checkpoints to
`/forge/vault/models/adapters/${ISO3}/${RUN}`, which needs a writable artefact
path. A profile relaxed until the job runs is not a control.

So this module states the containment that exists, as data, for three reasons.

**A ledger entry that records how a job ran must be true.** SAD 6.2 makes the
specification the unit of reproduction, and "ran in a rootless container with
no network" recorded against a job that ran as `nvidia` on a shared appliance
is a false record that survives longer than the estate.

**AC-S11 has to be reported against something real.** The criterion asks that
an executor attempting an outbound connection fails and that the attempt is
logged; it is recorded DEVIATED, and until now the deviation read "enforcing it
needs the appliance's kernel", which locates the gap in hardware that is on
order. The appliances arriving would not close it.

**A gap nobody has written down is a gap nobody owns.** `shortfall` names every
property the container model has and this one does not, in one place, so the
difference is a list somebody can work through rather than a paragraph in a
review.

**Two of the shortfalls close for about fifteen lines of configuration**, which
is only apparent once they are side by side. See `docs/fixes/`.

**What the manual specifies, precisely.** `slurm.conf` sets
`ProctrackType=proctrack/cgroup` and `TaskPlugin=task/cgroup`, and there is no
`cgroup.conf` anywhere in VLD-INF-SINDRI-001 Rev 3.3. That combination is worth
reading carefully, because it is easy to see the word `cgroup` twice and
conclude the jobs are constrained:

- `proctrack/cgroup` tracks a job's processes so they can all be **killed** at
  the end of it. It is a cleanup guarantee, not a resource limit, and it is a
  real one -- a job that forks and detaches does not survive its allocation.
- `task/cgroup` places tasks in cgroups so that `cgroup.conf` can constrain
  them. With no `cgroup.conf`, every constraint takes its default, and
  `ConstrainRAMSpace`, `ConstrainDevices`, `ConstrainCores` and
  `ConstrainSwapSpace` all default to **no**.

So `RealMemory=122880` is used for scheduling arithmetic and enforces nothing,
and `Gres=gpu:gb10:1` sets `CUDA_VISIBLE_DEVICES` for a job that chooses to
read it. On a three-node estate running one tenant's work this is a smaller
problem than it sounds; it is still not what "cgroup containment" is usually
taken to mean, and the difference matters when the next forge has two tenants.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

#: The administrative account VLD-INF-SINDRI-001 section 25 creates on all
#: three appliances. It is in `sudo`, it owns `/forge`, and it is the account
#: a job runs as -- because it is the account the job was submitted from and
#: nothing in the manual introduces another.
ADMIN_ACCOUNT: Final = "nvidia"


@dataclass(frozen=True, slots=True)
class Containment:
    """What holds a training job in, at one forge.

    Every field is a statement about the estate that somebody could go and
    check. Nothing here is aspirational: a field describing what the site
    intends to do would make this the same kind of document as the one it
    exists to correct.
    """

    #: A short name for the model, recorded in the ledger.
    model: str

    #: How the job's processes are grouped and ended.
    process_tracking: str

    #: Where the job may reach. `"none"` is a network namespace with nothing
    #: in it; anything else names what is reachable.
    outbound_network: str

    #: The account the job's processes run as.
    account: str

    #: Whether that account is dedicated to running jobs. An account that is
    #: also the administrator's is one where a compromised job inherits the
    #: administrator's authority -- including `sudo`, and including the
    #: credentials in that account's shell history and configuration.
    dedicated_account: bool

    #: Whether the job's memory use is constrained, rather than merely
    #: accounted for. Unconstrained, a job that allocates past `RealMemory`
    #: takes the appliance's other work with it.
    memory_constrained: bool

    #: Whether the job can reach only the accelerators it was granted.
    devices_constrained: bool

    #: Whether the root filesystem is read-only to the job.
    read_only_root: bool

    #: Whether the job inherits the submitter's environment. `--export=NONE`
    #: is what turns this off, and an inherited environment is how a token
    #: exported into a login shell reaches a training process.
    environment_isolated: bool

    #: Whether a process inside can gain privileges it did not start with.
    no_new_privileges: bool

    #: Where the job may write, and where it may only read.
    writable_paths: tuple[str, ...] = ()
    read_only_paths: tuple[str, ...] = ()

    def as_payload(self) -> dict[str, Any]:
        """The record of how a job ran, for the ledger entry that claims it."""
        return {
            "model": self.model,
            "processTracking": self.process_tracking,
            "outboundNetwork": self.outbound_network,
            "account": self.account,
            "dedicatedAccount": self.dedicated_account,
            "memoryConstrained": self.memory_constrained,
            "devicesConstrained": self.devices_constrained,
            "readOnlyRoot": self.read_only_root,
            "environmentIsolated": self.environment_isolated,
            "noNewPrivileges": self.no_new_privileges,
            "writablePaths": list(self.writable_paths),
            "readOnlyPaths": list(self.read_only_paths),
        }


#: What VLD-INF-SINDRI-001 Rev 3.3 actually builds. Read the module docstring
#: for why three of these fields are `False` rather than the `True` the phrase
#: "cgroup containment" would suggest.
SLURM_SHARED_VENV: Final = Containment(
    model="slurm-shared-venv",
    process_tracking="proctrack/cgroup, so a job's processes are killed with it",
    # Fabric 2 and Fabric 3. The job needs MLflow on ANDVARI at
    # 10.20.0.21:5000 and the corpus over NFS from the same host, so this is
    # not an oversight -- it is what the work requires.
    outbound_network="the site fabrics, including MLflow on ANDVARI",
    account=ADMIN_ACCOUNT,
    dedicated_account=False,
    memory_constrained=False,
    devices_constrained=False,
    read_only_root=False,
    environment_isolated=False,
    no_new_privileges=False,
    writable_paths=("/forge/vault/models/adapters", "/forge/runs", "/forge"),
    read_only_paths=(),
)


#: What `sandbox.SandboxProfile` describes, expressed the same way so the two
#: can be compared. Nothing builds this at Sindri.
CONTAINER_PER_JOB: Final = Containment(
    model="container-per-job",
    process_tracking="a container runtime, so the job is a process tree in its own namespace",
    outbound_network="none",
    account="nonroot (65532)",
    dedicated_account=True,
    memory_constrained=True,
    devices_constrained=True,
    read_only_root=True,
    environment_isolated=True,
    no_new_privileges=True,
    writable_paths=("the per-job working directory",),
    read_only_paths=("every artefact mount",),
)


#: How much of the difference is configuration rather than a new architecture.
#: Read as: these shortfalls close without a container runtime, an image for
#: the training stack, or a change to every batch script in Part 5.
CLOSEABLE_BY_CONFIGURATION: Final[frozenset[str]] = frozenset(
    {"dedicated_account", "memory_constrained", "devices_constrained", "environment_isolated"}
)


@dataclass(frozen=True, slots=True)
class Shortfall:
    """One property the intended model has and the one in force does not."""

    field_name: str
    consequence: str
    #: Whether closing it is configuration rather than a new execution model.
    closeable_by_configuration: bool

    def as_payload(self) -> dict[str, Any]:
        """The wire shape, for the gap register."""
        return {
            "property": self.field_name,
            "consequence": self.consequence,
            "closeableByConfiguration": self.closeable_by_configuration,
        }


#: What each missing property costs, in the terms the threat model uses. Kept
#: beside the fields rather than in a document, because a consequence written
#: down somewhere else is one that stops matching the field it describes.
CONSEQUENCES: Final[dict[str, str]] = {
    "dedicated_account": (
        f"a job runs as {ADMIN_ACCOUNT}, which is in `sudo` and owns /forge. A compromised "
        "dependency inherits the administrator's authority, which is threat T7 with no "
        "boundary in the way"
    ),
    "memory_constrained": (
        "RealMemory is scheduling arithmetic, not a limit. A job that allocates past it "
        "takes the appliance's other work with it, and on the `ring` partition that is "
        "the whole estate"
    ),
    "devices_constrained": (
        "`--gres=gpu:gb10:1` sets CUDA_VISIBLE_DEVICES for a job that chooses to read it. "
        "A job that does not is not prevented from reaching another job's accelerator"
    ),
    "environment_isolated": (
        "the job inherits the submitting shell's environment, which is how a token "
        "exported for one purpose reaches a training process (threat T6)"
    ),
    "read_only_root": (
        "a job can write outside its working directory, so a compromised dependency can "
        "modify the training stack itself in /forge/tools for every later run"
    ),
    "no_new_privileges": (
        "a setuid binary reachable from the job can raise its privileges, which is what "
        "makes the `sudo` membership above reachable rather than merely present"
    ),
    "outbound_network": (
        "the job reaches the site fabrics. Threat T11 says executors have no outbound "
        "network at all; here the executor sandbox layer does not exist, and the egress "
        "broker governs only the control plane"
    ),
}


def shortfall(
    in_force: Containment = SLURM_SHARED_VENV,
    intended: Containment = CONTAINER_PER_JOB,
) -> tuple[Shortfall, ...]:
    """Every property `intended` has that `in_force` does not, with its cost.

    Returns rather than raises. This is a gap register, not a gate: the estate
    is built the way it is built, and a function that refused to describe it
    would leave the difference undescribed rather than closed.
    """
    found: list[Shortfall] = []

    for name in (
        "dedicated_account",
        "memory_constrained",
        "devices_constrained",
        "environment_isolated",
        "read_only_root",
        "no_new_privileges",
    ):
        if getattr(intended, name) and not getattr(in_force, name):
            found.append(
                Shortfall(
                    field_name=name,
                    consequence=CONSEQUENCES[name],
                    closeable_by_configuration=name in CLOSEABLE_BY_CONFIGURATION,
                )
            )

    if intended.outbound_network == "none" and in_force.outbound_network != "none":
        found.append(
            Shortfall(
                field_name="outbound_network",
                consequence=CONSEQUENCES["outbound_network"],
                # Not configuration. The job needs MLflow, so closing this
                # means a container with an egress exception -- which is the
                # second model, not a setting.
                closeable_by_configuration=False,
            )
        )

    return tuple(found)


def for_site(site: str) -> Containment:
    """The containment in force at one forge.

    One site today. It takes an argument because the Forge Matrix has more
    than one, and because a second forge choosing containers is the change
    this module exists to make visible rather than silent.
    """
    del site
    return SLURM_SHARED_VENV


__all__ = [
    "ADMIN_ACCOUNT",
    "CLOSEABLE_BY_CONFIGURATION",
    "CONSEQUENCES",
    "CONTAINER_PER_JOB",
    "SLURM_SHARED_VENV",
    "Containment",
    "Shortfall",
    "for_site",
    "shortfall",
]
