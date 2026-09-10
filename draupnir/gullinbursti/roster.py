"""What each machine at this forge is for. RF-E20.

`docs/DEPLOYMENT.md` carries a "what runs where" table, and it is the table
somebody reads at three in the morning. It described an estate that differed
from the built one in four rows out of five. Each difference was small; the
aggregate was an operator reference that was wrong almost everywhere.

The manual is the authority on the estate, so this is VLD-INF-SINDRI-001 §2
transcribed with its provenance, and a test holds the deployment guide against
it. Same arrangement as `svalinn.site_egress`, for the same reason and with the
same limit: nothing in the pipeline can read the controlled document, so the
transcription is checked by hand at acceptance and everything downstream of it
is checked by machine.

**Each service carries whether a procedure actually installs it.** That
distinction is not decoration. §2's role table gives REGIN "Prometheus,
Grafana, Loki" and Procedure S11 installs `prometheus prometheus-alertmanager
grafana` — no Loki, and nothing else in the manual installs one. So the
deployment guide's claim that REGIN runs Loki was not invented there; it was
inherited from a role table that the procedures do not carry out. Deleting the
word from one document would have left the other two saying it.

**Contradictions inside the manual are recorded here, not resolved here.** They
belong to its author. What this module owes them is a place where they are
written down in a form that cannot be forgotten, and a test that fails if
anything in this repository starts relying on the wrong half.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Final

#: Where the transcription came from, so the next person can check it.
SOURCE_DOCUMENT: Final = "VLD-INF-SINDRI-001"
SOURCE_REVISION: Final = "3.3"
SOURCE_SECTION: Final = "2, host designations and roles"
TRANSCRIBED_ON: Final = "2026-09-08"


@dataclass(frozen=True, slots=True)
class Service:
    """One thing a machine runs, and whether anything installs it."""

    #: A normalised token, for comparing a document's prose against this.
    name: str
    #: What the manual calls it.
    label: str
    #: Where the manual says the machine has it.
    citation: str
    #: Whether a procedure in the manual actually installs it. `False` means
    #: the role table claims it and no procedure delivers it, which is a
    #: contradiction in the manual rather than a fault in the estate.
    installed: bool = True

    def as_payload(self) -> dict[str, Any]:
        """The wire shape."""
        return {
            "name": self.name,
            "label": self.label,
            "citation": self.citation,
            "installed": self.installed,
        }


@dataclass(frozen=True, slots=True)
class Machine:
    """One machine at the forge."""

    designation: str
    hardware: str
    services: tuple[Service, ...] = ()
    #: What the manual says the machine does, in its own words.
    role: str = ""

    @property
    def names(self) -> frozenset[str]:
        """Every service token this machine runs."""
        return frozenset(item.name for item in self.services)

    def as_payload(self) -> dict[str, Any]:
        """The wire shape."""
        return {
            "designation": self.designation,
            "hardware": self.hardware,
            "role": self.role,
            "services": [item.as_payload() for item in self.services],
        }


def _s(name: str, label: str, citation: str, *, installed: bool = True) -> Service:
    """Shorthand, because the roster is mostly this."""
    return Service(name=name, label=label, citation=citation, installed=installed)


SECTION_2: Final = "VLD-INF-SINDRI-001 Rev 3.3 section 2"

#: The estate at Sindri, from §2's designation table, with the procedures that
#: deliver each service named beside it.
SINDRI: Final[tuple[Machine, ...]] = (
    Machine(
        designation="DVALIN",
        hardware="DGX Spark",
        role="BAUGR node 1, NCCL rank 0. Substrate training. Primary storage "
        "consumer. Drives CON-A",
        services=(
            _s("slurmd", "Slurm compute daemon", "Procedure S12"),
            _s("dcgm", "DCGM exporter", "section 34 step 6"),
            _s("con-a", "the local console, driven over HDMI", SECTION_2),
        ),
    ),
    Machine(
        designation="DURIN",
        hardware="DGX Spark",
        role="BAUGR node 2, NCCL rank 1. Adapter training",
        services=(
            _s("slurmd", "Slurm compute daemon", "Procedure S12"),
            _s("dcgm", "DCGM exporter", "section 34 step 6"),
        ),
    ),
    Machine(
        designation="DAIN",
        hardware="DGX Spark",
        role="BAUGR node 3, NCCL rank 2. Adapter training",
        services=(
            _s("slurmd", "Slurm compute daemon", "Procedure S12"),
            _s("dcgm", "DCGM exporter", "section 34 step 6"),
        ),
    ),
    Machine(
        designation="ANDVARI",
        hardware="Mac mini M4 Pro",
        role="HODD vault host. MLflow tracking server. MinIO artefact store. NFS export",
        services=(
            _s("vault", "the HODD vault, on VAULT-01 over Thunderbolt 5", SECTION_2),
            _s("nfs", "the NFS export of the vault", SECTION_2),
            _s("mlflow", "the MLflow tracking server", SECTION_2),
            _s("minio", "MinIO, the artefact store", SECTION_2),
            # Not in section 2's role cell, and installed by Procedure S8. It
            # is what the MLflow tracking server stores its runs in, so the
            # role cell implies it without naming it.
            _s("postgresql", "PostgreSQL 16", "Procedure S8 step 7"),
        ),
    ),
    Machine(
        designation="ALVISS",
        hardware="Mac mini M4 Pro",
        role="MLX evaluation. Continuous integration runner. Ansible control node. "
        "DRAUPNIR application host",
        services=(
            _s("draupnir", "the DRAUPNIR control plane", SECTION_2),
            _s("mlx", "MLX evaluation", SECTION_2),
            _s("ansible", "the Ansible control node", SECTION_2),
            _s("ci", "the continuous integration runner", SECTION_2),
        ),
    ),
    Machine(
        designation="REGIN",
        hardware="Raspberry Pi 5, 16 GB",
        role="Slurm controller. Prometheus, Grafana, Loki. DNS and NTP. Drives CON-B",
        services=(
            _s("slurm", "slurmctld and slurmdbd", "Procedure S11 step 1"),
            _s("prometheus", "Prometheus", "Procedure S11 step 1"),
            _s("alertmanager", "Prometheus Alertmanager", "Procedure S11 step 1"),
            _s("grafana", "Grafana", "Procedure S11 step 1"),
            _s("dnsmasq", "dnsmasq", "Procedure S11 step 1"),
            _s("chrony", "chrony", "Procedure S11 step 1"),
            # Section 2's role cell names it. Procedure S11 installs
            # `prometheus prometheus-alertmanager grafana` and nothing else in
            # the manual installs a Loki or a log shipper of any kind.
            _s("loki", "Loki", f"{SECTION_2}, but no procedure installs it", installed=False),
            _s("con-b", "the operations console, driven over HDMI", SECTION_2),
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class Contradiction:
    """Two places in the estate documents that cannot both be right.

    Recorded rather than resolved. They belong to the documents' author; what
    this repository owes them is somewhere they cannot be forgotten, and a
    test that fails if anything here starts depending on the wrong half.
    """

    subject: str
    says: str
    and_says: str
    consequence: str
    #: Which half this repository behaves as though were true, and why.
    followed: str = ""

    def as_payload(self) -> dict[str, Any]:
        """The wire shape."""
        return {
            "subject": self.subject,
            "says": self.says,
            "andSays": self.and_says,
            "consequence": self.consequence,
            "followed": self.followed,
        }


#: What the estate documents disagree with themselves about. Found while
#: reconciling the deployment guide against them, which is the only reason
#: anybody read the two tables side by side.
CONTRADICTIONS: Final[tuple[Contradiction, ...]] = (
    Contradiction(
        subject="Loki, on REGIN",
        says=f"{SECTION_2} gives REGIN 'Prometheus, Grafana, Loki'",
        and_says=(
            "Procedure S11 step 1 installs `prometheus prometheus-alertmanager grafana` "
            "and no procedure anywhere installs Loki or a log shipper"
        ),
        consequence=(
            "the estate has no log aggregation at all, while three documents say it has. "
            "SAD 11E requires every log line to carry a run id, a site id and an actor, "
            "and DRAUPNIR emits exactly that -- to a file on ALVISS that nothing collects"
        ),
        followed=(
            "no aggregation. The runbook and the deployment guide read logs on the host, "
            "which is what actually works, and both now say so rather than implying a "
            "collector"
        ),
    ),
    Contradiction(
        subject="CON-B",
        says=f"{SECTION_2} says REGIN 'Drives CON-B'",
        and_says="section 6.3 says 'CON-B held as bench spare'",
        consequence=(
            "the Grafana dashboard amendments of RF-E13 and the console rotation of "
            "RF-E14 both target a display that one section says is not fitted"
        ),
        followed=(
            "fitted. VLD-WIR-SINDRI-001 cables it -- D-03 and D-04 to REGIN, P-13 to "
            "PDU-C -- and a bench spare is not given a power feed and two leads"
        ),
    ),
    Contradiction(
        subject="which machine drives CON-A",
        says=f"{SECTION_2} says DVALIN 'Drives CON-A'",
        and_says="section 6.2 lists the one HDMI lead as 'REGIN to CON-A'",
        consequence=(
            "CON-A exists to survive the network being gone (SAD Decision U2), which "
            "only holds if the appliance it watches is the one driving it. Driven from "
            "REGIN it is a second REGIN console and not a local view at all"
        ),
        followed=(
            "DVALIN. VLD-WIR-SINDRI-001 D-01 runs DVALIN HDMI to CON-A and D-05 powers "
            "it from DVALIN's USB-C, and section 2 agrees"
        ),
    ),
    Contradiction(
        subject="the image registry",
        says=(
            "`deploy/lib.sh` derives `registry.<site>.veldris.internal` and "
            "`rollout.sh` pulls the unit images from it"
        ),
        and_says=(
            "no machine in section 2 runs a container registry, and REGIN's dnsmasq is "
            "given no such record"
        ),
        consequence=(
            "the default image reference names a host that does not resolve, so a "
            "rollout fails at the pull with a DNS error rather than at a stage that "
            "explains itself. RF-04 in the companion register is the other half: "
            "nothing pushes an image to it either"
        ),
        followed=(
            "neither. The deployment guide now says the registry is not part of the "
            "estate and must be given with --registry until RF-04 lands"
        ),
    ),
)


def machine(designation: str, roster: Iterable[Machine] = SINDRI) -> Machine:
    """One machine by designation, case-insensitively."""
    wanted = designation.strip().upper()
    for item in roster:
        if item.designation == wanted:
            return item
    msg = f"{designation!r} is not a machine at this forge"
    raise KeyError(msg)


def declared_but_not_installed(
    roster: Iterable[Machine] = SINDRI,
) -> tuple[tuple[str, Service], ...]:
    """Every service a role table claims and no procedure delivers."""
    return tuple(
        (item.designation, service)
        for item in roster
        for service in item.services
        if not service.installed
    )


def designations(roster: Iterable[Machine] = SINDRI) -> tuple[str, ...]:
    """Every machine, in the order the manual lists them."""
    return tuple(item.designation for item in roster)


__all__ = [
    "CONTRADICTIONS",
    "SECTION_2",
    "SINDRI",
    "SOURCE_DOCUMENT",
    "SOURCE_REVISION",
    "SOURCE_SECTION",
    "TRANSCRIBED_ON",
    "Contradiction",
    "Machine",
    "Service",
    "declared_but_not_installed",
    "designations",
    "machine",
]
