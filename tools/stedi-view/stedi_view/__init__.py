"""CON-A, the local status view rendered by DVALIN.

S30, and Decision U2: "CON-A is not a small version of the console." It exists
because the DGX Spark has no baseboard management controller, so it is the only
console that survives a total network failure, and its entire value is that it
depends on nothing beyond the appliance it is attached to.

Consequently this package imports no HTTP client, nothing from `draupnir`, and
nothing from the web workspace. There is no shared code path with the console
that could later acquire a dependency on the API by someone editing a helper
they both use. `tests/unit/test_stedi_view.py` reads this source and asserts
it, because "we did not import that" is a property that decays.
"""

from stedi_view.readings import Reading, Status, all_readings

__all__ = ["Reading", "Status", "all_readings"]
__version__ = "0.1.0"
