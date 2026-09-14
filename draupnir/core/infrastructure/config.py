"""Runtime configuration, read from the environment.

Every setting carries the `DRAUPNIR_` prefix so that a container inherits
nothing by accident.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process configuration."""

    model_config = SettingsConfigDict(
        env_prefix="DRAUPNIR_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Literal["development", "test", "production"] = "development"
    site_id: str = "sindri"

    # 127.0.0.1 rather than `localhost`. On Windows `localhost` resolves to
    # ::1 first, the container publishes on IPv4 only, and psycopg waits out
    # the full connect timeout on the v6 attempt before falling back -- which
    # presents as the write path hanging while every read works, because the
    # async driver resolves differently. Naming the address removes the
    # difference rather than leaving it to a resolver.
    database_url: str = "postgresql+asyncpg://draupnir:draupnir@127.0.0.1:5432/draupnir"
    database_url_sync: str = "postgresql+psycopg://draupnir:draupnir@127.0.0.1:5432/draupnir"

    # Where the HODD vault is mounted. Empty means this installation has none,
    # which is the honest answer for a control plane without an estate: the
    # vault checks are then skipped rather than alarming hourly about an NFS
    # export that was never there.
    vault_root: str = ""

    # Where slurmrestd answers, empty when this installation has no scheduler.
    # A control plane without one is a legitimate configuration -- it is what a
    # developer machine is -- and the checks say so rather than alarming about
    # a REGIN that was never there.
    scheduler_url: str = ""

    # The generic resource type Slurm knows this estate's accelerator by, e.g.
    # `gb10`. Empty where the scheduler declares no type. Configuration rather
    # than a constant in `placement.SINDRI`, because a second forge in the
    # Forge Matrix will have different hardware and the same specifications.
    accelerator: str = ""

    # The TLS certificate and key the ingress terminates with. Empty means TLS
    # is not configured, and two things follow: `install.sh --check` refuses to
    # commission, and the cryptographic inventory reports TLS 1.3 as *not* in
    # use. SAD 9.5 says "TLS 1.3 only"; an inventory that asserted a transport
    # nobody terminates is exactly the failure AC-S16 exists to prevent
    # (RF-03).
    tls_certificate: str = ""
    tls_private_key: str = ""

    # Where the approvers' public keys are, one PEM per subject. SAD 9.4,
    # AC-S15.
    #
    # An approval's signature used to pass on being a non-empty string, and the
    # route supplied `approver_has_role: True` as a literal beside it (RF-06).
    # Verifying needs the approver's key, so the estate has to say where the
    # keys are; an unreadable directory raises rather than refusing every
    # approval silently.
    approver_key_store: str = "/etc/draupnir/approvers"

    # Where the plug-in signing keys are, and where the signatures over
    # installed distributions are recorded. SAD 9.3, AC-S7.
    #
    # `PkiVerifier.from_settings` refuses to build from a trust store it cannot
    # read, rather than building an empty one. An empty trust store refuses
    # every plug-in, which looks like a broken deployment -- and the fix
    # somebody reaches for is DRAUPNIR_DEV=1, turning a missing directory into
    # an estate that loads unsigned code (RF-02).
    plugin_trust_store: str = "/etc/draupnir/trust"
    plugin_signature_manifest: str = "/etc/draupnir/plugin-signatures.json"

    # Who issues the tokens this API trusts, and who they are addressed to.
    # MEGINGJORD is the identity provider for the Forge Matrix (SAD 9.3).
    #
    # Empty is not a default so much as an unset state: `create_app` refuses to
    # start when neither is configured and DRAUPNIR_DEV is unset, because a
    # control plane with no way to authenticate anybody answers 401 to
    # everything -- which reads as a broken deployment rather than a missing
    # setting, and gets debugged instead of configured (RF-01).
    oidc_issuer: str = ""
    oidc_audience: str = ""

    # The authorisation-code flow the console signs in through (RF-03).
    #
    # A public client with PKCE, so `oidc_client_secret` is normally empty:
    # there is no secret to distribute to a static bundle, and PKCE is what
    # replaces one. It is here for a confidential registration, where the
    # provider insists.
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_scope: str = "openid profile email"

    # Where the provider sends the browser back. Empty derives it from the
    # request, which is right behind the console's own proxy; set it where the
    # public URL differs from what the API sees.
    oidc_redirect_uri: str = ""

    # Derived from the issuer when empty, by the usual convention. Named
    # because a provider is entitled to put them elsewhere.
    oidc_authorization_endpoint: str = ""
    oidc_token_endpoint: str = ""

    # Where the issuer publishes its public keys. Derived from the issuer when
    # empty, by the discovery convention; set it where a provider puts them
    # somewhere else.
    oidc_jwks_url: str = ""

    # Clock skew tolerated on `exp`, `nbf` and `iat`. Capped at 60s by the
    # verifier whatever is configured here: beyond that the setting stops being
    # skew tolerance and starts being an extension of a revoked token's life.
    oidc_leeway_seconds: int = 60

    # The appliances cabled into the ring, when the forge declares them.
    # Empty means all of them, which is the ordinary case.
    #
    # Named rather than counted: ring membership is which machines have a DAC
    # cable between them, and a count cannot say which two. VLD-WIR-SINDRI-001
    # section 7.4 recables the two survivors of an appliance failure as a
    # direct pair; with gap G6 holding zero spare QSFP56 cables, that is the
    # configuration this estate will actually be in.
    # A comma separated list, and a plain string rather than a tuple on
    # purpose. pydantic-settings JSON-decodes a complex field before any
    # validator runs, so a tuple here would oblige an operator to write
    # `["durin","dain"]` into a shell-style file -- during a recovery, with one
    # machine already dead. `ring_member_names()` splits it.
    ring_members: str = ""

    # Where the site's Prometheus answers, empty when this installation has no
    # collector to read. Same shape and the same reason as the scheduler above:
    # a developer machine has none, and the panels then say "unmeasured, no
    # telemetry client is configured" rather than alarming about a REGIN that
    # was never there.
    #
    # The control plane never collects from this address, only reads. The DCGM
    # exporter on each appliance is the collector (VLD-INF-SINDRI-001 section
    # 34 step 6) and Prometheus on REGIN is the store; a second path to the
    # same GPUs would be a second thing to keep in step with a driver upgrade.
    prometheus_url: str = ""

    # Where MEGINGJORD answers, empty where this forge is not federated. The
    # worker has carried this since RF-07 to anchor the chain head; the API
    # needs it too, because readiness has to be able to say whether the
    # wide-area link is up (RF-17). `deploy/install.sh` already writes it into
    # the one environment file both units read, so this names a setting that
    # was already there rather than adding one.
    registry_url: str = ""

    # Where an OpenTelemetry collector answers, empty where this forge has
    # none -- which is the ordinary case and not a fault (RF-18). Spans were
    # collected into a list and discarded, so "spans from edge through
    # orchestrator to driver boundary" was a shape a test could assert and a
    # trace nobody could look at.
    #
    # An unconfigured tracer still discards, and still clears: a list nobody
    # drains is a memory leak with a plausible reason.
    otlp_endpoint: str = ""

    # Fraction of traces exported. One per request is a lot of documents to
    # carry for a signal whose value is the shape rather than the census, and
    # an exporter that cannot be turned down is one that gets turned off.
    otlp_sample: float = 1.0

    # The exporter's names for what is being read. Configuration because they
    # are the exporter's, they change between its versions, and section 48.2
    # already warns that an update can rename things underneath this estate --
    # a name only a release could correct is one nobody corrects.
    metric_gpu_temperature: str = "DCGM_FI_DEV_GPU_TEMP"
    metric_throttle_reasons: str = "DCGM_FI_DEV_CLOCK_THROTTLE_REASONS"
    metric_fabric_bandwidth: str = "draupnir_fabric_bus_bandwidth_gbps"
    metric_fabric_baseline: str = "draupnir_fabric_baseline_gbps"

    object_store_endpoint: str = "127.0.0.1:9000"
    object_store_access_key: str = "draupnir"
    object_store_secret_key: str = "draupnir-dev-secret"  # noqa: S105
    object_store_bucket: str = "draupnir"
    object_store_secure: bool = False

    api_host: str = "127.0.0.1"
    api_port: int = 8000
    log_level: str = Field(default="info")

    def ring_member_names(self) -> tuple[str, ...]:
        """The appliances cabled into the ring, or empty for all of them."""
        return tuple(part.strip() for part in self.ring_members.split(",") if part.strip())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, read once."""
    return Settings()
