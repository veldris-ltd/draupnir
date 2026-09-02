"""The egress broker: every outbound call is declared, logged, or refused.

Threat T11 is "unapproved egress by a dependency or framework", and the reason
it needs a broker rather than a firewall rule is in the wording. A firewall
answers "may this host reach that host". The question that matters here is "why
is this run reaching that host, and who said it could" -- and a dependency
that starts phoning home in a minor version bump satisfies the first question
and fails the second.

So a call declares four things and a broker records them: destination, purpose,
run id, and the policy that approved it. A call to a destination nobody
declared fails. Not warns, not proxies with a header: fails.

**The teacher-model destination is deliberately absent.** Threat T3 is
distillation-time exfiltration of corpus content, and SAD Q3 puts distillation
out of scope for Release 1. AC-S3 requires that the destination is absent from
the allow list and that a call to it fails here with a logged refusal. There is
a test that fails if anybody adds it, which is the point: the destination
becoming allow-listed is exactly the change that must not happen quietly.

Executors have no outbound network namespace at all (see `sandbox`), so this
broker governs the control plane. Two layers, because the executor sandbox is
the one an escaping dependency cannot argue with, and the broker is the one
that produces a record.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final
from urllib.parse import urlparse


class EgressError(Exception):
    """Raised when an outbound call may not be made."""


class UndeclaredDestinationError(EgressError):
    """Raised when a call names a destination that is not allow-listed. AC-S3, AC-S11."""

    def __init__(self, destination: str, purpose: str, run_id: str | None) -> None:
        """Name what was reached for, and on whose behalf."""
        self.destination = destination
        self.purpose = purpose
        self.run_id = run_id
        run = f" for run {run_id}" if run_id else ""
        super().__init__(
            f"egress to {destination!r}{run} was refused: it is not in the allow list. "
            f"Purpose given: {purpose!r}. Every outbound call declares a destination, a "
            "purpose, a run and an approving policy, and an undeclared destination "
            "fails rather than being proxied (threat T11)."
        )


class UndeclaredCallError(EgressError):
    """Raised when a call does not declare what the broker requires."""


@dataclass(frozen=True, slots=True)
class Destination:
    """One allow-listed place the control plane may reach, and why."""

    host: str
    purpose: str
    #: The policy that approved it. A destination with no approving policy is
    #: a destination somebody added.
    approving_policy: str
    scheme: str = "https"
    #: Set where the destination is reachable only from one site.
    site: str | None = None

    def matches(self, url: str) -> bool:
        """Whether `url` names this destination."""
        parsed = urlparse(url)
        return parsed.hostname == self.host and parsed.scheme == self.scheme

    def as_payload(self) -> dict[str, Any]:
        """The wire shape, for the evidence pack."""
        return {
            "host": self.host,
            "scheme": self.scheme,
            "purpose": self.purpose,
            "approvingPolicy": self.approving_policy,
            "site": self.site,
        }


#: The Release 1 allow list. Short on purpose: each entry is a destination
#: somebody argued for, and the list is evidence rather than configuration.
ALLOW_LIST: Final[tuple[Destination, ...]] = (
    Destination(
        host="megingjord.veldris.internal",
        purpose="chain-head anchoring, policy pull, release metadata push",
        approving_policy="federation/2026.01",
    ),
    Destination(
        host="huggingface.co",
        purpose="base model and tokeniser acquisition, pinned by revision",
        approving_policy="acquisition/2026.01",
    ),
    Destination(
        host="pypi.org",
        purpose="dependency resolution at image build time",
        approving_policy="supply-chain/2026.01",
    ),
)

#: The destination AC-S3 requires to be absent. Named here so the refusal can
#: be specific, and so that `test_the_teacher_destination_is_not_allow_listed`
#: has something to assert about rather than an absence.
#:
#: Distillation is out of scope for Release 1 (SAD Q3). The broker is built and
#: refuses the call. Reinstate threat T3 when distillation enters scope -- and
#: adding this host to `ALLOW_LIST` is a decision with a threat model attached,
#: not a configuration change.
TEACHER_DESTINATION: Final = "api.teacher-model.example"


@dataclass(frozen=True, slots=True)
class Call:
    """One outbound call, as it must be declared."""

    url: str
    purpose: str
    run_id: str | None
    approving_policy: str
    requested_at: datetime

    def __post_init__(self) -> None:
        """Refuse a call that has not declared what the broker records."""
        missing = [
            name
            for name, value in (
                ("url", self.url),
                ("purpose", self.purpose),
                ("approving_policy", self.approving_policy),
            )
            if not value
        ]
        if missing:
            msg = (
                f"an outbound call declares its destination, purpose, run and approving "
                f"policy; {', '.join(missing)} was not given. A call the broker cannot "
                "describe is a call nobody can account for afterwards."
            )
            raise UndeclaredCallError(msg)
        if self.requested_at.tzinfo is None:
            msg = "egress timestamps carry an explicit offset (SAD 11E.2)"
            raise UndeclaredCallError(msg)

    @property
    def host(self) -> str:
        """The host this call reaches."""
        return urlparse(self.url).hostname or ""


@dataclass(frozen=True, slots=True)
class Record:
    """What the broker logged about one call, permitted or refused."""

    call: Call
    permitted: bool
    reason: str
    destination: Destination | None = None

    def as_log_context(self) -> dict[str, Any]:
        """The structured log line. Carries no header, body or credential."""
        return {
            "event": "egress",
            "permitted": self.permitted,
            "host": self.call.host,
            "scheme": urlparse(self.call.url).scheme,
            "purpose": self.call.purpose,
            "runId": self.call.run_id,
            "approvingPolicy": self.call.approving_policy,
            "requestedAt": self.call.requested_at.isoformat(),
            "reason": self.reason,
        }


@dataclass
class EgressBroker:
    """Decides and records every outbound call the control plane makes."""

    allow_list: tuple[Destination, ...] = ALLOW_LIST
    records: list[Record] = field(default_factory=list)

    def destination_for(self, url: str) -> Destination | None:
        """The allow-list entry matching `url`, if there is one."""
        for candidate in self.allow_list:
            if candidate.matches(url):
                return candidate
        return None

    def check(self, call: Call) -> Record:
        """Decide one call and record the decision. Refusals are logged too.

        Returns rather than raises, so that the permitted and refused paths
        look the same to a caller that has to log either way. `request` raises.
        """
        destination = self.destination_for(call.url)
        if destination is None:
            record = Record(
                call=call,
                permitted=False,
                reason=f"{call.host or call.url!r} is not in the allow list",
            )
            self.records.append(record)
            return record

        if destination.approving_policy != call.approving_policy:
            record = Record(
                call=call,
                permitted=False,
                destination=destination,
                reason=(
                    f"the call cites policy {call.approving_policy!r} and "
                    f"{destination.host} is approved under "
                    f"{destination.approving_policy!r}. A destination approved for one "
                    "purpose is not approved for another."
                ),
            )
            self.records.append(record)
            return record

        record = Record(
            call=call,
            permitted=True,
            destination=destination,
            reason=f"{destination.host} approved under {destination.approving_policy}",
        )
        self.records.append(record)
        return record

    def request(self, call: Call) -> Record:
        """Decide one call, raising on refusal. AC-S3, AC-S11."""
        record = self.check(call)
        if not record.permitted:
            raise UndeclaredDestinationError(call.host or call.url, call.purpose, call.run_id)
        return record

    @property
    def refusals(self) -> tuple[Record, ...]:
        """Every refused call. What AC-S3 and AC-S11 inspect."""
        return tuple(item for item in self.records if not item.permitted)

    def log(self) -> tuple[Mapping[str, Any], ...]:
        """Every decision, in order, for the audit trail."""
        return tuple(item.as_log_context() for item in self.records)


def allow_listed_hosts(allow_list: Iterable[Destination] = ALLOW_LIST) -> tuple[str, ...]:
    """Every host the control plane may reach, sorted. For the evidence pack."""
    return tuple(sorted(item.host for item in allow_list))
