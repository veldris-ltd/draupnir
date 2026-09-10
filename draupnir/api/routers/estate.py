"""What the appliances are actually doing, physically. SAD 11.3.

The thermal and throttle signals of SAD 11.3 have a collector — the DCGM
exporter, installed on every appliance by VLD-INF-SINDRI-001 section 34 step 6
— and a store, Prometheus on REGIN. What they did not have was a route out of
that store into anything DRAUPNIR renders, so CON-B's thermal dashboard derived
"under load" and "idle" from run state instead. Run state is not a temperature.
An appliance that is idle because it is thermally throttled reads `idle`, in
grey, which is the one case the panel exists for.

This is the route. It reads and nothing else: GULLINBURSTI reports capacity and
health (SAD 11A.1), the exporter collects, and a control plane that opened its
own path to the GPUs would be a second thing to keep in step with a driver
upgrade.

**It answers 200 with reasons rather than 503 with nothing.** A telemetry
outage is a degraded mode (SAD 11.2), and a panel that renders one unavailable
tile beside five real readings is more use than a panel that renders an error
because one query failed. Every reading is a value or a reason; a reading with
neither cannot be constructed.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import Field

from draupnir.api import telemetry
from draupnir.api.deps import Estate, Guarded, now
from draupnir.api.guards import needs
from draupnir.api.schemas import Wire
from draupnir.core.infrastructure.config import get_settings
from draupnir.motsognir.placement import estate_for
from draupnir.svalinn.roles import Permission

router = APIRouter(tags=["operations"])


class Measurement(Wire):
    """One reading, or the reason there is not one.

    `value` and `reason` are both nullable and exactly one is set. They are on
    one model rather than two because a client that had to pick a model would
    pick by testing `value`, and `0.0` is falsy — which is how a throttle
    bitmask of zero, meaning nothing is throttling, becomes an error tile.
    """

    metric: str = Field(description="What was measured, e.g. `gpu_temperature`.")
    subject: str = Field(description="The appliance, or `baugr` for the ring fabric.")
    unit: str = Field(description="The unit of `value`. `C`, `GB/s`, or `bitmask`.")
    value: float | None = Field(
        default=None,
        description=(
            "The measurement. Null where there is none — never zero. A zero here is a "
            "real reading and means the measured quantity is zero."
        ),
    )
    reason: str | None = Field(
        default=None,
        description="Why there is no value. Null when there is one. Never both, never neither.",
    )


class EstateTelemetryResponse(Wire):
    """Every reading behind CON-B's thermal and fabric dashboards."""

    read_at: str = Field(description="When this answer was assembled, with an explicit offset.")
    source: str = Field(description="The collector these came from.")
    readings: list[Measurement] = Field(
        description=(
            "One entry per appliance per metric, plus the fabric. Appliances Prometheus "
            "knows nothing about are present with a reason, because a panel that simply "
            "omitted a machine would look identical to a smaller estate."
        )
    )


@router.get(
    "/estate/telemetry",
    summary="Appliance thermal, throttle and fabric measurements",
    operation_id="getEstateTelemetry",
    response_model=EstateTelemetryResponse,
)
@needs(Permission.READ)
async def get_estate_telemetry(ctx: Guarded, estate: Estate) -> EstateTelemetryResponse:
    """Read the site's measurements back out of its own collector."""
    del ctx
    settings = get_settings()
    appliances = [item.name for item in estate_for(settings.site_id).appliances]

    reading = estate.read(appliances, now=now())
    telemetry.log(
        "estate.telemetry.read",
        measured=len(reading.measured),
        unmeasured=len(reading.unmeasured),
    )
    return EstateTelemetryResponse(
        read_at=reading.read_at.isoformat(),
        source=reading.source,
        readings=[
            Measurement(
                metric=item.metric,
                subject=item.subject,
                unit=item.unit,
                value=item.value,
                reason=item.reason or None,
            )
            for item in reading.readings
        ],
    )
