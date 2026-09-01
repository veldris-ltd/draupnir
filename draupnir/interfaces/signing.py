"""The plug-in signature verification hook.

SAD 5.2 gives SVALINN "plug-in signature verification"; SAD 9.3 makes a signed
plug-in a control rather than a convention. The verifier itself is built in
Prompt 6. This module is the seam it will slot into, so that the loader is
written against verification from the first build rather than having it
threaded through later.

The default verifier reports every plug-in as unverified. The loader then
refuses to load it unless `DRAUPNIR_DEV=1`, in which case it loads and logs a
warning naming the distribution. That is the whole of the development
concession, and it is one environment variable wide.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class SignatureStatus:
    """The outcome of verifying one plug-in distribution."""

    verified: bool
    #: Who signed it, once there is a signature to read.
    signer: str | None = None
    #: Why verification failed, or why it was not attempted.
    reason: str | None = None


@runtime_checkable
class SignatureVerifier(Protocol):
    """Verifies that an installed distribution was signed by a trusted key."""

    def verify(self, distribution: str, version: str) -> SignatureStatus:
        """Return the signature status of an installed distribution."""
        ...


class UnverifiedVerifier:
    """The default. Verifies nothing and says so.

    Named for what it does rather than for what it will eventually be, because
    a class called `PkiVerifier` that verifies nothing is how a development
    concession reaches production unnoticed.
    """

    def verify(self, distribution: str, version: str) -> SignatureStatus:
        """Report the distribution as unverified, with the reason."""
        del version
        return SignatureStatus(
            verified=False,
            reason=(
                f"no signature verifier is configured, so {distribution} is unverified. "
                "The Veldris PKI verifier is built in Prompt 6 (SAD 9.3, Decision S9)."
            ),
        )
