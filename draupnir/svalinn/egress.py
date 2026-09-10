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

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final, Protocol
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
    #: The port, where the host runs more than one thing. REGIN runs both
    #: slurmrestd and Prometheus, and they are different destinations with
    #: different approvals -- a broker that could not tell them apart would let
    #: a console holding the telemetry approval cancel a job.
    #:
    #: `None` means any port, which is right for a public host reached on its
    #: scheme's default and wrong for anything on the management fabric.
    port: int | None = None
    #: Set where the destination is reachable only from one site.
    site: str | None = None

    #: Whether a call to this host leaves the estate. Internal destinations do
    #: not: they are on the site fabrics or over the federation link, and the
    #: site router's outbound policy (VLD-INF-SINDRI-001 section 10.5) neither
    #: sees them nor should list them. Recorded because the reconciliation in
    #: `site_egress` has to tell "the router permits this" from "the router is
    #: not in the path", and treating the second as the first would put
    #: internal hostnames in an internet egress policy.
    traverses_site_router: bool = True

    #: Why this destination is not reachable yet, where that is the case. An
    #: entry with a gap note is a declared permission for a path that does not
    #: exist -- which is worth having, and worth not mistaking for a live one.
    gap: str = ""

    #: The host whose bare name implies this one. Set on a redirect target, so
    #: the evidence says why a second entry exists rather than leaving it to
    #: look like duplication.
    redirect_from: str = ""

    def matches(self, url: str) -> bool:
        """Whether `url` names this destination."""
        parsed = urlparse(url)
        if parsed.hostname != self.host or parsed.scheme != self.scheme:
            return False
        return self.port is None or parsed.port == self.port

    def as_payload(self) -> dict[str, Any]:
        """The wire shape, for the evidence pack."""
        return {
            "host": self.host,
            "scheme": self.scheme,
            "port": self.port,
            "purpose": self.purpose,
            "approvingPolicy": self.approving_policy,
            "site": self.site,
            "traversesSiteRouter": self.traverses_site_router,
            "redirectFrom": self.redirect_from,
            "gap": self.gap,
        }


#: MEGINGJORD's purpose and approving policy, named so the composition root
#: can cite them without transcribing a string that has to match.
#:
#: One entry for the federation registry and the identity provider, because at
#: Sindri they are one host reached over one TLS endpoint with no credential on
#: the request. Splitting them the way REGIN is split would need the allow list
#: to distinguish by path, which is a mechanism worth adding when there is a
#: distinction it makes -- and here there is not: a caller that can reach the
#: JWKS can reach the registry, because it is the same socket.
FEDERATION_PURPOSE: Final = (
    "chain-head anchoring, policy pull, release metadata push, the JWKS this API "
    "verifies bearer tokens against, the authorisation-code exchange the console "
    "signs in through, and the readiness probe that reports whether the wide-area "
    "link is up"
)
FEDERATION_POLICY: Final = "federation/2026.01"

#: REGIN's scheduler, named rather than written twice. The allow-list entry and
#: every caller cite the same constants, so a purpose that grows -- as it did
#: when readiness started probing the link (RF-17) -- grows in one place. The
#: policy is what the broker compares, and it is deliberately not the
#: federation one: a caller approved to reach MEGINGJORD is not thereby
#: approved to submit a job.
SCHEDULING_PURPOSE: Final = (
    "job submission, status and cancellation over slurmrestd, and the readiness "
    "probe that reports whether the scheduler is answering"
)
SCHEDULING_POLICY: Final = "scheduling/2026.01"

#: The Release 1 allow list. Short on purpose: each entry is a destination
#: somebody argued for, and the list is evidence rather than configuration.
ALLOW_LIST: Final[tuple[Destination, ...]] = (
    # -- internal. These never reach the site router. ----------------------
    #
    # Deliberately not site scoped, unlike every hostname in `deploy/`. SAD 11A
    # makes MEGINGJORD one registry for the whole Forge Matrix, and
    # VLD-INF-SINDRI-001 Rev 3.3 puts it on Veldris_NXT rather than at a forge,
    # so `megingjord.sindri.veldris.internal` would name a thing that should not
    # exist.
    Destination(
        host="megingjord.veldris.internal",
        purpose=FEDERATION_PURPOSE,
        approving_policy=FEDERATION_POLICY,
        traverses_site_router=False,
        gap=(
            "the WireGuard link to Veldris_NXT is not built, so this name does not "
            "resolve and this permission is for a path that does not exist yet. It "
            "terminates on REGIN rather than here (RF-E21): REGIN already carries "
            "wireguard-tools, it is the machine on both fabrics, and it returns from a "
            "power cut without anybody typing a FileVault password at a console. So "
            "the control plane reaches MEGINGJORD through a route, and what has to "
            "exist is a dnsmasq record on REGIN and a route on this host -- neither of "
            "which changes anything the broker decides."
        ),
    ),
    # `http` and a port, both of them corrections. `motsognir.slurmrest/v1`
    # calls `http://regin.sindri.veldris.internal:6820`, and this entry
    # declared https on any port -- so a brokered submission would have been
    # refused as undeclared, and the refusal would have looked like the
    # scheduler being down.
    Destination(
        host="regin.sindri.veldris.internal",
        scheme="http",
        port=6820,
        purpose=SCHEDULING_PURPOSE,
        approving_policy=SCHEDULING_POLICY,
        traverses_site_router=False,
    ),
    # The same host again, and deliberately a second entry rather than a wider
    # purpose on the first. Prometheus is a different port, a different scheme
    # and a different reason, and the broker checks the approving policy on
    # every call -- so two entries mean a driver holding the scheduling
    # approval cannot use it to read metrics, and a console holding this one
    # cannot use it to cancel a job. One entry with the purposes concatenated
    # would have made both possible.
    #
    # `http`, not a typo. Prometheus on REGIN serves the management fabric
    # without TLS (VLD-INF-SINDRI-001 section 34 step 7), and `matches` compares
    # the scheme, so declaring https here would refuse every real call. The
    # port is what separates this from the entry above it.
    Destination(
        host="regin.sindri.veldris.internal",
        scheme="http",
        port=9090,
        purpose="reading appliance thermal, throttle and fabric measurements",
        approving_policy="observability/2026.01",
        traverses_site_router=False,
    ),
    # -- model and tokeniser acquisition -----------------------------------
    #
    # Two entries for one operation, because the operation is two requests.
    # `huggingface.co` answers the metadata and then redirects the weight
    # download elsewhere; an allow list holding only the first refuses the
    # transfer at exactly the point where the transfer starts, and the failure
    # reads as a corrupt download rather than a policy decision. RF-E17.
    Destination(
        host="huggingface.co",
        purpose="base model and tokeniser acquisition, pinned by revision",
        approving_policy="acquisition/2026.01",
    ),
    Destination(
        host="cdn-lfs.huggingface.co",
        purpose="the weight blobs themselves, where huggingface.co redirects them",
        approving_policy="acquisition/2026.01",
        redirect_from="huggingface.co",
    ),
    # -- dependency resolution, at image build time ------------------------
    Destination(
        host="pypi.org",
        purpose="dependency resolution at image build time",
        approving_policy="supply-chain/2026.01",
    ),
    Destination(
        host="files.pythonhosted.org",
        purpose="the wheels themselves, where pypi.org serves them from",
        approving_policy="supply-chain/2026.01",
        redirect_from="pypi.org",
    ),
    Destination(
        host="registry.npmjs.org",
        purpose="the console's dependency resolution at image build time",
        approving_policy="supply-chain/2026.01",
    ),
    # -- the base images the build starts from -----------------------------
    #
    # None of these three is Docker Hub, and none of them is in section 10.5,
    # which permits `registry-1.docker.io` and its blob host. `docker/` names
    # ghcr.io for uv, gcr.io for distroless and cgr.dev for the Chainguard node
    # and nginx images. Building on the estate as specified would fail at the
    # router, and it would fail looking like a broken build. RF-E17.
    Destination(
        host="ghcr.io",
        purpose="the uv builder image (docker/api.Dockerfile)",
        approving_policy="supply-chain/2026.01",
    ),
    Destination(
        host="pkg-containers.githubusercontent.com",
        purpose="the layers themselves, where ghcr.io serves them from",
        approving_policy="supply-chain/2026.01",
        redirect_from="ghcr.io",
    ),
    Destination(
        host="gcr.io",
        purpose="the distroless runtime image (docker/api.Dockerfile)",
        approving_policy="supply-chain/2026.01",
    ),
    Destination(
        host="storage.googleapis.com",
        purpose="the layers themselves, where gcr.io serves them from",
        approving_policy="supply-chain/2026.01",
        redirect_from="gcr.io",
    ),
    Destination(
        host="cgr.dev",
        purpose="the Chainguard node and nginx images (docker/web.Dockerfile)",
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


class Sends(Protocol):
    """The part of an HTTP client the broker wraps."""

    def get(self, url: str, *, params: Mapping[str, str] | None = ...) -> Any:
        """Perform one request."""
        ...

    def post(
        self,
        url: str,
        *,
        json: Mapping[str, Any] | None = ...,
        headers: Mapping[str, str] | None = ...,
    ) -> Any:
        """Submit one document."""
        ...


@dataclass
class BrokeredClient:
    """An HTTP client that cannot make a call the broker has not decided.

    The broker's value is only realised where something is obliged to consult
    it. `EgressBroker` on its own is a decision procedure nobody is required to
    invoke, which is exactly the shape a dependency phoning home in a minor
    version bump slips past. This is the obligation: a caller holding one of
    these has no route to the network that does not go through `request` first.

    It wraps a client rather than being one, so the transport stays swappable
    and so a test can supply something that never opens a socket. The purpose
    and policy are fixed at construction, because a client that took them per
    call would let the caller choose its own approval.
    """

    inner: Sends
    purpose: str
    approving_policy: str
    broker: EgressBroker = field(default_factory=EgressBroker)
    run_id: str | None = None
    #: Injected so a test does not have to wait for a clock. SAD 11E.2 wants an
    #: explicit offset, and `datetime.now()` without one is the usual way a log
    #: line acquires a local time nobody can correlate.
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))

    def get(self, url: str, *, params: Mapping[str, str] | None = None) -> Any:
        """Decide the call, then make it. A refused call is never made."""
        self._decide(url)
        return self.inner.get(url, params=params)

    def post(
        self,
        url: str,
        *,
        json: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        """Decide the submission, then make it. RF-07.

        The client had only `get`, so the one caller that submits rather than
        fetches -- `federation.RemoteRegistry`, anchoring the chain head to
        MEGINGJORD -- had no brokered client it could be given. Either the
        anchor path went unwired or it would have taken a raw `httpx.Client`
        around the broker entirely, which is threat T11 exactly.

        Outbound submission is the direction that matters most here. A fetch
        leaks a hostname; a post leaks a body, and AC-S14 is about what a
        federation payload carries.
        """
        self._decide(url)
        return self.inner.post(url, json=json, headers=headers)

    def _decide(self, url: str) -> None:
        """Put the call to the broker. Raises rather than returning a verdict.

        One place, so that a method added later cannot quietly be the one that
        forgets: a client with an undecided route to the network is the whole
        defect this type exists to make impossible.
        """
        self.broker.request(
            Call(
                url=url,
                purpose=self.purpose,
                run_id=self.run_id,
                approving_policy=self.approving_policy,
                requested_at=self.clock(),
            )
        )


def allow_listed_hosts(allow_list: Iterable[Destination] = ALLOW_LIST) -> tuple[str, ...]:
    """Every host the control plane may reach, sorted. For the evidence pack."""
    return tuple(sorted(item.host for item in allow_list))
