"""Installed plug-ins, versions and signature status. SAD 8.1, operator.

`GET /v1/plugins`.

The signature status is the point of the endpoint. AC-S7 requires an unsigned
plug-in to fail to load, and this is where an operator sees which distributions
loaded, which were refused and why -- so that "the loader refused something" is
visible before somebody wonders why a driver is missing.

Refusals are returned in the body rather than suppressed. A plug-in that failed
to load is exactly what somebody is looking for when they call this.
"""

from __future__ import annotations

from fastapi import APIRouter

from draupnir.api import telemetry
from draupnir.api.deps import Guarded
from draupnir.api.guards import needs
from draupnir.api.schemas import PluginList, PluginOut
from draupnir.core.plugins import PluginRegistry
from draupnir.svalinn.roles import Permission

router = APIRouter(tags=["operations"])

#: Discovered once. Plug-ins are installed into the environment, so the set
#: cannot change without a restart, and rediscovering per request would import
#: every driver module on every call.
_REGISTRY: PluginRegistry | None = None


def registry() -> PluginRegistry:
    """The process-wide plug-in registry."""
    # One process-wide discovery, by design: plug-ins are installed into the
    # environment and cannot change without a restart.
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = PluginRegistry.discover()
    return _REGISTRY


def reset_registry() -> None:
    """Forget the discovered registry. For a test that installs its own."""
    global _REGISTRY
    _REGISTRY = None


@router.get(
    "/plugins",
    summary="Installed plug-ins and signature status",
    operation_id="listPlugins",
    response_model=PluginList,
)
@needs(Permission.READ)
async def list_plugins(ctx: Guarded) -> PluginList:
    """Every loaded plug-in, and every distribution the loader refused."""
    del ctx
    found = registry()

    items = [
        PluginOut(
            name=str(plugin.name),
            group=plugin.group,
            distribution=plugin.distribution,
            version=plugin.distribution_version,
            capabilities=sorted(plugin.capabilities),
            signature_verified=plugin.signature.verified,
            signer=plugin.signature.signer,
            reason=plugin.signature.reason,
        )
        for group in (
            "draupnir.train",
            "draupnir.merge",
            "draupnir.eval",
            "draupnir.export",
            "draupnir.schedule",
            "draupnir.store",
            "draupnir.policy",
        )
        for plugin in found.all(group)
    ]

    telemetry.log("plugins.listed", loaded=len(items), refused=len(found.failures))
    return PluginList(
        items=sorted(items, key=lambda item: item.name),
        failures=[f"{failure.name}: {failure.reason}" for failure in found.failures],
    )
