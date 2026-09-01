"""The conformance suite any driver can run.

SAD 11E.3: "Every driver against the conformance harness ... and the harness
published for third parties." This subpackage is that harness. It depends on
`draupnir.interfaces` and nothing else in DRAUPNIR, so a driver author installs
one package and gets the vocabulary, the Protocols and the suite that proves
their driver satisfies them.

    from draupnir.interfaces.testing import ScheduleDriverConformance

    class TestMyDriver(ScheduleDriverConformance):
        @pytest.fixture
        def driver(self):
            return MyDriver()

The pytest classes need pytest; the checks in `harness` do not, and can be run
from a plain script.
"""

from draupnir.interfaces.testing.fixtures import SAMPLE_SPEC_MAPPING, sample_spec
from draupnir.interfaces.testing.harness import (
    Finding,
    NetworkAccessError,
    check_driver,
    check_job_driver,
    check_schedule_driver,
    describe,
    no_network,
)
from draupnir.interfaces.testing.suite import (
    DriverConformance,
    JobDriverConformance,
    ScheduleDriverConformance,
)

__all__ = [
    "SAMPLE_SPEC_MAPPING",
    "DriverConformance",
    "Finding",
    "JobDriverConformance",
    "NetworkAccessError",
    "ScheduleDriverConformance",
    "check_driver",
    "check_job_driver",
    "check_schedule_driver",
    "describe",
    "no_network",
    "sample_spec",
]
