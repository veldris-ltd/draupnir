"""The secrets broker: short-lived tokens, fetched at job start, never written down.

Threat T6 is "credentials leaked into a checkpoint or log", and the mitigation
has two halves. This module is the first: secrets are brokered as short-lived
tokens fetched at job start and never written into a job environment file or a
rendered configuration. `scanning` is the second half, which catches what
escapes anyway.

The design follows from where secrets actually leak. Not from a vault being
broken into, but from a secret being *placed somewhere durable* on the way to
being used: an environment file on a shared filesystem, a rendered YAML in a
working directory, a `JobPlan.environment` mapping that gets logged when a
submission fails. Every one of those is a place a secret is written at rest, by
the control plane, in the ordinary course of working.

So a `Lease` never yields its value into anything that is serialised. It has no
`as_payload`, its `__repr__` and `__str__` are redacted, and `redact` exists so
that a rendered plan can be checked before it is submitted. The value comes out
through one method, whose name says what is happening.

Leases are short. A token that outlives the job it was issued for is a token
somebody can use afterwards, and the fact that nobody did is not a control.
"""

from __future__ import annotations

import secrets as _secrets
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Final

#: How long a brokered token lives. Long enough for a job to start and fetch
#: what it needs; far short of a training run, because a token valid for the
#: whole of an eighteen hour job is a token valid for eighteen hours.
DEFAULT_TTL: Final = timedelta(minutes=15)

#: The most a caller may ask for. A lease longer than this is refused rather
#: than granted with a warning: "short lived" stops meaning anything the first
#: time an exception is made for a job that found it inconvenient.
MAX_TTL: Final = timedelta(hours=1)

#: What a redacted value is replaced with. Fixed-length and obviously not a
#: secret, so a redacted log line cannot be mistaken for a real value or have
#: its length used to identify what was there.
REDACTED: Final = "[redacted]"


class SecretsError(Exception):
    """Raised when a secret cannot be brokered or has been mishandled."""


class LeaseExpiredError(SecretsError):
    """Raised when a lease is used after it has run out."""

    def __init__(self, name: str, expired_at: datetime) -> None:
        """Name the lease and when it lapsed."""
        self.name = name
        self.expired_at = expired_at
        super().__init__(
            f"the lease for {name!r} expired at {expired_at.isoformat()}. Brokered "
            "tokens are short lived by design; fetch a new one rather than extending "
            "this one."
        )


class SecretMaterialisedError(SecretsError):
    """Raised when a secret value is found somewhere durable.

    The failure T6 is actually about. Raised by `assert_no_secrets`, which a
    driver's rendered plan is checked against before submission.
    """

    def __init__(self, where: str, names: Iterable[str]) -> None:
        """Name where it was found and which leases it belongs to."""
        self.where = where
        self.names = tuple(sorted(names))
        super().__init__(
            f"a brokered secret value appears in {where}: {', '.join(self.names)}. "
            "Secrets are fetched by the job at start and are never written into a job "
            "environment file or a rendered configuration (threat T6). Pass the "
            "lease reference and let the executor redeem it."
        )


@dataclass(frozen=True)
class Lease:
    """One brokered secret. The value is reachable, and never serialisable.

    Not `slots=True`: the redaction below relies on `__repr__` and `__str__`
    being overridden, and a dataclass with slots and a custom repr is the same
    thing with more ways to get it wrong.
    """

    name: str
    #: An opaque handle the executor redeems. Safe to log, safe to put in a
    #: rendered plan: it names a lease, it is not the secret.
    reference: str
    issued_at: datetime
    expires_at: datetime
    #: The run this was issued for. A lease that names no run cannot be
    #: revoked when that run ends.
    run_id: str
    _value: str = field(repr=False, default="")

    def __post_init__(self) -> None:
        """Refuse a lease that cannot be accounted for or expired."""
        if self.expires_at.tzinfo is None or self.issued_at.tzinfo is None:
            msg = "lease timestamps carry an explicit offset (SAD 11E.2)"
            raise SecretsError(msg)
        if self.expires_at <= self.issued_at:
            msg = f"the lease for {self.name!r} expires before it is issued"
            raise SecretsError(msg)
        if self.expires_at - self.issued_at > MAX_TTL:
            msg = (
                f"a lease may last at most {MAX_TTL}; {self.name!r} was asked for "
                f"{self.expires_at - self.issued_at}. A token valid for the length of a "
                "training run is a token valid for the length of a training run."
            )
            raise SecretsError(msg)

    def __repr__(self) -> str:
        """Redacted. A repr is what ends up in a traceback."""
        return f"Lease(name={self.name!r}, reference={self.reference!r}, value={REDACTED})"

    def __str__(self) -> str:
        """Redacted. A str is what ends up in an f-string in a log line."""
        return self.__repr__()

    def expired(self, now: datetime) -> bool:
        """Whether this lease has run out."""
        return now >= self.expires_at

    def reveal(self, now: datetime) -> str:
        """Return the secret value. The only way out, and it is named.

        `reveal` rather than `value` or `get`, because a call site reading
        `lease.reveal(now)` is one a reviewer stops at, and `lease.value` is
        one they do not.
        """
        if self.expired(now):
            raise LeaseExpiredError(self.name, self.expires_at)
        return self._value

    def as_payload(self) -> dict[str, Any]:
        """The lease, without the secret. Safe to log and to serialise."""
        return {
            "name": self.name,
            "reference": self.reference,
            "runId": self.run_id,
            "issuedAt": self.issued_at.isoformat(),
            "expiresAt": self.expires_at.isoformat(),
        }


@dataclass
class SecretsBroker:
    """Issues short-lived leases against a backing store.

    The store is a mapping here. In deployment it is a hardware-backed key
    store (SAD 9.5, key custody), reached over mTLS; the shape of what this
    module hands out does not change with it.
    """

    store: Mapping[str, str] = field(default_factory=dict, repr=False)
    ttl: timedelta = DEFAULT_TTL
    issued: list[Lease] = field(default_factory=list, repr=False)

    def issue(
        self, name: str, *, run_id: str, now: datetime, ttl: timedelta | None = None
    ) -> Lease:
        """Issue a lease for `name`, for one run, expiring shortly.

        Called at job start rather than at submission. A lease minted when a
        run is queued has already expired by the time an allocation arrives,
        and the pressure that creates is to lengthen the lease.
        """
        if name not in self.store:
            msg = (
                f"no secret named {name!r} is held. A missing secret is refused rather "
                "than substituted with an empty value, which fails later and looks "
                "like a permissions problem at the far end."
            )
            raise SecretsError(msg)
        if now.tzinfo is None:
            msg = "lease timestamps carry an explicit offset (SAD 11E.2)"
            raise SecretsError(msg)

        lease = Lease(
            name=name,
            reference=f"lease:{_secrets.token_urlsafe(16)}",
            issued_at=now,
            expires_at=now + (ttl or self.ttl),
            run_id=run_id,
            _value=self.store[name],
        )
        self.issued.append(lease)
        return lease

    def active(self, now: datetime) -> tuple[Lease, ...]:
        """Every lease that has not yet expired."""
        return tuple(item for item in self.issued if not item.expired(now))

    def values(self) -> tuple[str, ...]:
        """Every secret value held. For redaction and for the leak check."""
        return tuple(value for value in self.store.values() if value)

    # -- the half of T6 that catches mistakes -----------------------------

    def redact(self, text: str) -> str:
        """Replace every held secret value in `text`.

        Applied to executor output before it is stored. A secret that reaches a
        log has already escaped, and redacting on the way in is what stops it
        being copied onwards into every downstream index.
        """
        cleaned = text
        for value in sorted(self.values(), key=len, reverse=True):
            cleaned = cleaned.replace(value, REDACTED)
        return cleaned

    def assert_no_secrets(self, where: str, payload: Any) -> None:
        """Raise if any held secret value appears in `payload`.

        Called on a rendered `JobPlan` before submission. The check is on the
        serialised form rather than on the mapping's values, because a secret
        interpolated into a command string is the case a values-only check
        misses and the one that actually happens.
        """
        rendered = repr(payload)
        found = [name for name, value in self.store.items() if value and value in rendered]
        if found:
            raise SecretMaterialisedError(where, found)


def brokered_environment(leases: Iterable[Lease]) -> dict[str, str]:
    """The environment a job is started with: references, never values.

    This is what makes the requirement true rather than intended. The executor
    receives lease references and redeems them itself at start; there is no
    point at which the control plane writes a secret into a job environment
    file, because it never has the values in a form it could write.
    """
    return {f"DRAUPNIR_LEASE_{item.name.upper()}": item.reference for item in leases}
