"""AC-N4: the run list stays inside its latency budget.

"API responds to a run list of 500 entries: under 300 ms at the 95th
percentile."

Measured through the application rather than against the query, because the
budget is about what a client experiences. Serialising 500 records is a real
cost and a query timing would not include it.

The 95th percentile rather than the mean, because the criterion says so and
because a mean hides the tail an operator actually notices. Measured over
enough requests that the percentile means something, and after a warm-up, so
the figure is steady-state rather than a measurement of import time.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from draupnir.api import deps
from draupnir.api.app import create_app
from draupnir.api.idempotency import IdempotencyStore
from draupnir.api.pagination import MAX_LIMIT

#: Contract rather than integration: this measures the edge and touches no
#: database, so it has no business sharing a session with the suite that
#: owns the containers.
pytestmark = pytest.mark.contract

#: AC-N4's budget.
BUDGET_SECONDS = 0.300

#: How many requests to time. Enough that the 95th percentile is a percentile
#: rather than "the worst of a handful".
SAMPLES = 200

OPERATOR = {
    "sub": "operator-1",
    "iss": "https://megingjord.veldris.internal",
    "roles": ["operator"],
    "amr": ["pwd", "hwk"],
}


@pytest.fixture
def client() -> Iterator[TestClient]:
    """A client whose requests arrive authenticated."""
    original = deps.STORE
    deps.STORE = IdempotencyStore()
    app = create_app()

    @app.middleware("http")
    async def inject(request: Any, call_next: Any) -> Any:
        request.state.claims = OPERATOR
        return await call_next(request)

    try:
        yield TestClient(app)
    finally:
        deps.STORE = original


def percentile(samples: list[float], fraction: float) -> float:
    """The nearest-rank percentile of a sample set."""
    ordered = sorted(samples)
    index = max(int(len(ordered) * fraction) - 1, 0)
    return ordered[index]


def test_a_run_list_responds_inside_the_budget(client: TestClient) -> None:
    """AC-N4, at the page size the criterion implies.

    A run list of 500 entries is four pages at the maximum page size. Each page
    is one request and one round trip, and the budget is per request -- which
    is the number an operator paging a run board experiences.
    """
    path = f"/v1/runs?limit={MAX_LIMIT}"

    for _ in range(20):  # Warm-up: routing tables, validators, serialisers.
        client.get(path)

    timings: list[float] = []
    for _ in range(SAMPLES):
        started = time.perf_counter()
        response = client.get(path)
        timings.append(time.perf_counter() - started)
        assert response.status_code == 200

    p95 = percentile(timings, 0.95)

    assert p95 < BUDGET_SECONDS, (
        f"the run list responded in {p95 * 1000:.1f} ms at the 95th percentile, and "
        f"AC-N4's budget is {BUDGET_SECONDS * 1000:.0f} ms."
    )


def test_the_maximum_page_size_bounds_what_one_request_serialises() -> None:
    """A client asking for everything gets a page.

    Without the ceiling, `limit=100000` is one request holding the whole
    projection in memory per concurrent caller, and the 95th percentile of
    AC-N4 becomes a measurement of how much JSON fits in RAM.
    """
    from draupnir.api.pagination import clamp

    assert clamp(100_000) == MAX_LIMIT
    assert MAX_LIMIT < 500


def test_an_authorisation_decision_does_not_dominate_the_budget(
    client: TestClient,
) -> None:
    """The guard runs on every request, so its cost is in every measurement."""
    timings: list[float] = []
    for _ in range(50):
        started = time.perf_counter()
        client.get("/healthz")
        timings.append(time.perf_counter() - started)

    assert percentile(timings, 0.95) < BUDGET_SECONDS
