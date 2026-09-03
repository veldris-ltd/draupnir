"""The Forge Matrix. SAD 11A, and the reason the console has a site switcher.

`GET /v1/sites` is the one read in this API that is not scoped to a single
site, and the exception is deliberate and narrow: a switcher cannot be built
from a list that contains only the site you are already on. What it returns is
the *registry* -- identifier, name, location, control plane endpoint, anchor
state -- and no run, corpus, artefact or ledger data whatsoever. So it is the
list of scopes rather than an aggregate across them, which is exactly the
distinction AC-U11 draws when it forbids an unscoped aggregate view.

The anchor state is on this response because the console needs it before it
renders anything else. A partitioned site continues to train and cannot release
(Decision S8), and a console that discovers that only when a publish returns
409 has already let an approver believe the action was available.
"""

from __future__ import annotations

from fastapi import APIRouter

from draupnir.api import telemetry
from draupnir.api.deps import Guarded, Reading
from draupnir.api.guards import needs
from draupnir.api.schemas import SitePage
from draupnir.svalinn.roles import Permission

router = APIRouter(tags=["sites"])


@router.get(
    "/sites",
    summary="The registered sites",
    operation_id="listSites",
    response_model=SitePage,
)
@needs(Permission.READ)
async def list_sites(ctx: Guarded, reading: Reading) -> SitePage:
    """Every site this control plane knows, with its anchor state. AC-F18."""
    del ctx
    page = await reading.sites()
    telemetry.log("sites.listed", count=len(page.items))
    return page
