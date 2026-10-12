"""The link to MEGINGJORD, built in one place for the API and the worker. G8.

Both need it. The worker anchors the chain (SAD 11A.3); the API registers a
release at publish, with proof it was anchored, and reads back the estate
posture it was registered under (VLD-SAD-MEGINGJORD-001 AC-X5, AC-I1i). One
builder, so the two cannot disagree about which forge they speak for or which
server they trust.

Here, in a composition root, because it puts three modules together that may
not import each other: GULLINBURSTI's protocol, SVALINN's mutually
authenticated transport (SAD 9.5) and the egress broker every outbound call goes
through (threat T11).
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from draupnir.gullinbursti.protocol import Bundle, Link
from draupnir.svalinn import transport
from draupnir.svalinn.egress import FEDERATION_POLICY, FEDERATION_PURPOSE, BrokeredClient

#: How long one call to MEGINGJORD may take. It crosses a WireGuard link to
#: another site; long enough for that, short enough that a worker tick or a
#: publish does not stall behind an unreachable server.
TIMEOUT_SECONDS: Final = 15.0


def megingjord_link(bundle: str | Path, *, timeout: float = TIMEOUT_SECONDS) -> Link | None:
    """The link for the bundle at `bundle`, or `None` where none is configured.

    A bundle that is configured and unusable raises (`protocol.BundleError`,
    `transport.TransportError`), each saying what to do: a forge told to
    federate that cannot is misconfigured, and calling that "no federation
    link" would send an operator looking for a tunnel that is fine.
    """
    if not str(bundle).strip():
        return None

    import httpx

    loaded = Bundle.load(Path(bundle))
    context = transport.client_context(
        certificate=str(loaded.client_certificate),
        private_key=str(loaded.client_key),
        server_ca=str(loaded.server_ca),
    )
    return Link.from_bundle(
        loaded,
        BrokeredClient(
            inner=httpx.Client(timeout=timeout, verify=context),
            purpose=FEDERATION_PURPOSE,
            approving_policy=FEDERATION_POLICY,
        ),
    )


def main(argv: list[str] | None = None) -> int:
    """`python -m draupnir.api.federation BUNDLE`: load it as the units will.

    What `deploy/install.sh` runs before it starts anything: the bundle read,
    its keys loaded and its TLS material put in a context, with nothing sent.
    It says which forge it would speak for and where, or what to put right.
    """
    import argparse
    import sys

    from draupnir.gullinbursti.protocol import BundleError

    parser = argparse.ArgumentParser(description="Load a MEGINGJORD bundle as GULLINBURSTI does.")
    parser.add_argument("bundle")
    arguments = parser.parse_args(argv)
    try:
        link = megingjord_link(arguments.bundle)
    except (BundleError, transport.TransportError) as error:
        sys.stderr.write(f"{error}\n")
        return 1
    if link is None:
        sys.stderr.write("no bundle was named\n")
        return 1
    sys.stdout.write(
        f"GULLINBURSTI speaks for {link.bundle.site_name} ({link.site_id}), signing as "
        f"{link.bundle.key_id}, to {link.bundle.endpoint}\n"
    )
    return 0


__all__ = ["TIMEOUT_SECONDS", "main", "megingjord_link"]


if __name__ == "__main__":
    raise SystemExit(main())
