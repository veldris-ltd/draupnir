"""Ports: the seven Protocols of SAD 8.2 and the conformance harness.

Knows interfaces, never implementations (SAD 11B). Nothing here imports the
core, the edge or any module, and `.importlinter` holds it to that -- which is
what lets a third party depend on this package alone to write a driver.

    naming     entry point names and version negotiation (SAD 10.3)
    types      the vocabulary the Protocols are written in
    protocols  the seven interfaces
    signing    the plug-in signature verification hook
    testing    the conformance suite, published for third parties
"""

from draupnir.interfaces.naming import InterfaceName, InterfaceNameError, supported_majors
from draupnir.interfaces.protocols import (
    CURRENT_MAJOR,
    PROTOCOL_FOR_GROUP,
    Driver,
    EvalDriver,
    ExportDriver,
    JobDriver,
    MergeDriver,
    PolicyDriver,
    ScheduleDriver,
    StoreDriver,
    TrainDriver,
)
from draupnir.interfaces.signing import SignatureStatus, SignatureVerifier
from draupnir.interfaces.types import GROUPS

__all__ = [
    "CURRENT_MAJOR",
    "GROUPS",
    "PROTOCOL_FOR_GROUP",
    "Driver",
    "EvalDriver",
    "ExportDriver",
    "InterfaceName",
    "InterfaceNameError",
    "JobDriver",
    "MergeDriver",
    "PolicyDriver",
    "ScheduleDriver",
    "SignatureStatus",
    "SignatureVerifier",
    "StoreDriver",
    "TrainDriver",
    "supported_majors",
]
