"""Ask each dependency whether it will accept this application.

`install.sh` opened a TCP socket and called that a dependency check. A socket
answering proves the machine is on and something is listening; it proves
nothing about whether DRAUPNIR can use it. At Sindri the difference is not
academic. VLD-INF-SINDRI-001 Procedure S8 step 7 creates the `mlflow` database
and no other, starts MinIO with no credentials and no bucket, and leaves
Homebrew's PostgreSQL bound to localhost. Every one of those passes a socket
test and none of them lets the control plane start.

**Four failures, four remedies.** They are reported separately because they
send an operator to four different places:

    unreachable    the port is closed, the host is not listening on the
                   address this machine can reach, or nothing is mounted where
                   the vault should be. Infrastructure.
    auth-refused   the credentials in secrets.env are wrong, or `pg_hba.conf`
                   has no rule for this client. The operator, then the DBA.
    missing        the service is there and the database, the bucket or the
                   vault marker inside it is not. Provisioning: Procedure S13,
                   once.
    ok             it will accept us.

The fourth is the scheduler, checked for reachability only: whether it will
accept us depends on a JWT that SVALINN issues as a lease, and an installer
carrying a scheduler credential in order to check one would be a worse outcome
than an unchecked one.

The third dependency is the HODD vault, and it is here because SAD 11.3 gives
the control plane a vault capacity alarm at 85 per cent while
VLD-INF-SINDRI-001 Procedure S9 mounts the vault on the three appliances and
not on ALVISS. `config.py` treats an unset vault root as "this installation has
no vault" and skips the checks, which is the right default and the wrong answer
at a forge that has one: the signal SAD 11.3 requires would be silently absent
rather than absent and saying so.

There is a fifth state and it is not a failure. On a first commissioning the
credentials do not exist yet -- `install.sh` creates `secrets.env` empty and
the operator fills it in afterwards -- so the deep checks cannot run. That is
reported as `unverified` with the reason, rather than passed over in silence or
treated as a fault. Re-running `--check` after the credentials are in place is
what turns it into one of the four above, and the deployment guide now says so.

**Why the application's own drivers.** psycopg and minio rather than `psql` and
`mc`: the question is whether *this* code can connect, and the honest way to
ask it is with the client that will be doing the connecting. It also means the
check needs nothing installed that the control plane does not already need.
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal
from urllib.parse import urlsplit, urlunsplit

Verdict = Literal["ok", "unreachable", "auth-refused", "missing", "unverified"]

#: The database every PostgreSQL cluster has. Connecting to it with the same
#: credentials is what separates "these credentials are wrong" from "these
#: credentials are fine and the `draupnir` database is not there".
#:
#: SQLSTATE would be the obvious way to tell those apart -- 28P01 against 3D000
#: -- and it does not work: psycopg 3 raises a bare `OperationalError` with
#: `sqlstate` unset for every connection-time failure, because the failure
#: happens before a session exists to carry one. The server's message says
#: which, but PostgreSQL localises it through `lc_messages`, so reading it is a
#: check that works until somebody's server is configured in another language.
#: Probing is neither.
_MAINTENANCE_DATABASE: Final = "postgres"

#: The S3 error codes that mean "your keys are wrong" rather than "no bucket".
#: `AccessDenied` is included because a scoped key that cannot see the bucket
#: is a credential to fix, not a bucket to create.
_S3_AUTH_CODES: Final = frozenset(
    {"SignatureDoesNotMatch", "InvalidAccessKeyId", "AccessDenied", "AuthorizationHeaderMalformed"}
)

#: The slurmrestd API version `motsognir.slurmrest/v1` speaks. Named here so the
#: check asks for the surface the driver will actually use: a server offering
#: only an older version is a scheduler the control plane cannot drive, and
#: finding that out at commissioning is much cheaper than at the first run.
_SLURMRESTD_VERSION: Final = "v0.0.40"

#: Long enough for a loaded host on the storage fabric, short enough that a
#: dead one does not hold up a maintenance window.
TIMEOUT_SECONDS: Final = 5


@dataclass(frozen=True, slots=True)
class Result:
    """What one dependency said, and what to do about it."""

    dependency: str
    verdict: Verdict
    detail: str
    remedy: str

    @property
    def failed(self) -> bool:
        """Whether this stops a commissioning.

        `unverified` does not: a first commissioning has no credentials yet by
        design, and refusing to proceed would make the documented order
        impossible to follow.
        """
        return self.verdict in {"unreachable", "auth-refused", "missing"}

    def render(self) -> str:
        """Render one line for install.sh to parse.

        Pipe separated because a detail contains spaces and a remedy contains
        a command, so neither can be the separator.
        """
        return f"{self.dependency}|{self.verdict}|{self.detail}|{self.remedy}"


def _libpq_url(url: str) -> str:
    """Strip SQLAlchemy's dialect suffix so libpq recognises the URL."""
    return url.replace("postgresql+psycopg://", "postgresql://", 1).replace(
        "postgresql+asyncpg://", "postgresql://", 1
    )


def _has_credential(url: str) -> bool:
    """Whether a URL carries a password rather than the empty placeholder.

    A URL with a role and no password is what the installer writes before the
    operator has been near it, and connecting with one would report
    `auth-refused` for a credential nobody has supplied yet.
    """
    _, _, rest = url.partition("://")
    userinfo, at, _ = rest.rpartition("@")
    if not at:
        return False
    _, colon, password = userinfo.partition(":")
    # `PASSWORD` is the literal placeholder docs/DEPLOYMENT.md tells the
    # operator to replace, so a URL still carrying it has no credential yet.
    return bool(colon and password and password != "PASSWORD")  # noqa: S105


def check_postgresql(url: str, *, timeout: int = TIMEOUT_SECONDS) -> Result:
    """Connect as the application would, and classify the refusal by probing.

    Four questions in order, each narrowing the last:

      1. Does anything accept a TCP connection? No, and it is `unreachable`;
         nothing below can distinguish a closed port from a wrong password.
      2. Does the target database accept us? Yes, and it is `ok`.
      3. Does the maintenance database accept the same credentials? Yes, so the
         credentials are good and the target is `missing`.
      4. Otherwise the server is answering and refusing us: `auth-refused`.

    Step 2 comes before step 3 so that a role with no access to `postgres` but
    full access to `draupnir` is reported as working, which it is.

    The server's own first line is carried into `detail` whichever branch is
    taken, so an operator sees what PostgreSQL actually said even where this
    classification is wrong.
    """
    name = "postgresql"
    if not url:
        return Result(
            name, "unverified", "no database URL is configured", "set DRAUPNIR_DATABASE_URL_SYNC"
        )
    if not _has_credential(url):
        return Result(
            name,
            "unverified",
            "the URL carries no password, so this is a first commissioning",
            "put the credentials in secrets.env and run --check again",
        )

    libpq = _libpq_url(url)
    host, port = _host_and_port(libpq)
    if not _port_accepts(host, port, timeout):
        return Result(
            name,
            "unreachable",
            f"nothing accepted a connection on {host}:{port}",
            "check the host is up and listening on the address this machine reaches",
        )

    target = _try_connect(libpq, timeout)
    if target is None:
        return Result(name, "ok", f"connected to {_database_of(libpq)}", "")

    maintenance = _try_connect(_with_database(libpq, _MAINTENANCE_DATABASE), timeout)
    if maintenance is None:
        return Result(
            name,
            "missing",
            f"the credentials work and there is no `{_database_of(libpq)}` database. {target}",
            "create the role and the database (VLD-INF-SINDRI-001 Procedure S13)",
        )

    return Result(
        name,
        "auth-refused",
        f"the server is listening and refused us. {target}",
        "check secrets.env, then pg_hba.conf on ANDVARI for a rule matching this host",
    )


def _host_and_port(url: str) -> tuple[str, int]:
    """The address a libpq URL points at."""
    split = urlsplit(url)
    return split.hostname or "127.0.0.1", split.port or 5432


def _database_of(url: str) -> str:
    """The database a libpq URL names."""
    return urlsplit(url).path.lstrip("/") or "postgres"


def _with_database(url: str, database: str) -> str:
    """The same URL against a different database."""
    return urlunsplit(urlsplit(url)._replace(path=f"/{database}"))


def _port_accepts(host: str, port: int, timeout: int) -> bool:
    """Whether anything is listening. Asked before any credential is offered."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _try_connect(url: str, timeout: int) -> str | None:
    """None when the connection succeeded, else the server's first line."""
    import psycopg

    try:
        with psycopg.connect(url, connect_timeout=timeout) as connection:
            connection.execute("SELECT 1")
    except Exception as error:
        first = str(error).strip().splitlines()
        return first[0] if first else type(error).__name__
    return None


def check_object_store(
    endpoint: str,
    *,
    access_key: str,
    secret_key: str,
    bucket: str,
    secure: bool,
    timeout: int = TIMEOUT_SECONDS,
) -> Result:
    """Ask the object store whether the bucket is there and we may see it."""
    name = "object-store"
    if not endpoint:
        return Result(
            name, "unverified", "no endpoint is configured", "set DRAUPNIR_OBJECT_STORE_ENDPOINT"
        )
    if not access_key or not secret_key:
        return Result(
            name,
            "unverified",
            "no access key is configured, so this is a first commissioning",
            "put the credentials in secrets.env and run --check again",
        )

    import urllib3
    from minio import Minio
    from minio.error import S3Error

    client = Minio(
        endpoint,
        access_key=access_key,
        secret_key=secret_key,
        secure=secure,
        http_client=urllib3.PoolManager(
            timeout=urllib3.Timeout(connect=timeout, read=timeout), retries=False
        ),
    )

    try:
        present = client.bucket_exists(bucket)
    except S3Error as error:
        if error.code in _S3_AUTH_CODES:
            return Result(
                name,
                "auth-refused",
                f"the store refused these credentials ({error.code})",
                "check DRAUPNIR_OBJECT_STORE_ACCESS_KEY and _SECRET_KEY in secrets.env",
            )
        return Result(name, "unreachable", f"{error.code}: {error.message}", "check the endpoint")
    except Exception as error:
        return Result(
            name,
            "unreachable",
            f"{type(error).__name__}: {str(error).strip()[:120]}",
            "check the host is up and listening on the address this machine reaches",
        )

    if not present:
        return Result(
            name,
            "missing",
            f"the store is running and has no `{bucket}` bucket",
            "create it with versioning enabled (VLD-INF-SINDRI-001 Procedure S13)",
        )
    return Result(name, "ok", f"bucket {bucket} is present", "")


def check_vault(root: str) -> Result:
    """Is the vault mounted, is it the vault, and how full is it?

    Three states rather than two, because the middle one is the dangerous one.
    A directory where the vault should be is worse than no vault at all: the
    store creates an artefact's parent directories on write, so a run would
    train and stage its weights onto the control plane's own disk, and the real
    mount returning later would hide the evidence. The `.hodd-vault` marker
    exists to tell that apart from a mount, and this is where it earns its
    keep -- at commissioning, rather than after a run.

    Read-only is enough here and enough for the worker's capacity duty. The
    host mount is read-write because `vault_admin.py reconcile --apply` ingests
    into the vault, and the runbook sends an operator to run it here.
    """
    name = "hodd-vault"
    if not root:
        return Result(
            name,
            "unverified",
            "no vault root is configured, so the SAD 11.3 capacity alarm has no source",
            "set DRAUPNIR_VAULT_ROOT if this forge has a vault; leave it unset if it does not",
        )

    from draupnir.hodd.reconcile import (
        VAULT_MARKER,
        VaultNotInitialisedError,
        VaultUnavailableError,
        require_vault,
    )
    from draupnir.hodd.stores import PosixStoreDriver, StoreError

    store = PosixStoreDriver(root=Path(root), local_site="")

    # `require_vault` already draws the distinction this check exists to
    # report, and raises a different type for each. Asking it and translating
    # is better than asking `mounted`, which answers both with False.
    try:
        require_vault(store)
    except VaultUnavailableError:
        return Result(
            name,
            "unreachable",
            f"nothing is mounted at {root}",
            "restore the NFS mount from ANDVARI (runbook section 4)",
        )
    except VaultNotInitialisedError:
        return Result(
            name,
            "missing",
            (
                f"{root} exists and holds no {VAULT_MARKER}, so it is a directory where "
                "the vault should be rather than the vault"
            ),
            "restore the mount; do not initialise over it unless this is a new vault",
        )

    try:
        free, total = store.free_bytes(), store.total_bytes()
    except StoreError as refusal:
        return Result(name, "unreachable", f"capacity unreadable: {refusal}", "check the mount")

    used = (total - free) / total if total else 0.0
    return Result(name, "ok", f"mounted, {used:.0%} used, {free / 1e9:.1f} GB free", "")


def check_scheduler(
    url: str,
    *,
    token: str = "",
    user_name: str = "",
    timeout: int = TIMEOUT_SECONDS,
) -> Result:
    """Is slurmrestd there, and does it have the partitions this expects?

    Reachability first, and without a credential. What can be established from
    an unauthenticated request is that something answers on the management
    fabric, which is the failure an operator can most often act on -- REGIN
    down, the wrong port, or slurmrestd not enabled. A 401 counts as reachable
    and is worth saying out loud: it means slurmrestd is running and configured
    for JWT, which is the state a correctly built REGIN is in.

    Then, **when a token is configured**, the partitions. `Partition.EXPORT`
    used to be sent to Slurm as a partition name and VLD-INF-SINDRI-001 section
    34 declares two -- `adapters` and `ring` -- so a run placed on `export`
    queued and was rejected days later. `export` is now a venue rather than a
    partition, and this checks the two that really are ones. A run submitted to
    a partition that does not exist is a configuration mistake, and this is
    where a configuration mistake belongs.

    The token comes from `secrets.env` like the database password: the same
    file, read the same way, at the same point in the commissioning. Without
    one this reports what it could check and says what it could not, rather
    than claiming the partitions are fine.
    """
    name = "scheduler"
    if not url:
        return Result(
            name,
            "unverified",
            "no scheduler is configured, so nothing can be dispatched",
            "set DRAUPNIR_SCHEDULER_URL; leave it unset only on a host with no estate",
        )

    split = urlsplit(url)
    host, port = split.hostname or "", split.port or (443 if split.scheme == "https" else 80)
    if not host:
        return Result(name, "unreachable", f"{url} is not a URL with a host", "check the setting")

    if not _port_accepts(host, port, timeout):
        return Result(
            name,
            "unreachable",
            f"nothing accepted a connection on {host}:{port}",
            "check REGIN is up and slurmrestd is running (VLD-INF-SINDRI-001 Procedure S11)",
        )

    import urllib.error
    import urllib.request

    # An open port is not the answer. A port answering is compatible with any
    # service at all being there, and reporting "the scheduler is up" because
    # something replied is how a check earns being ignored: MinIO on the wrong
    # port answers 403 to this quite happily.
    ping = f"{url.rstrip('/')}/slurm/{_SLURMRESTD_VERSION}/ping"
    body = b""
    try:
        with urllib.request.urlopen(ping, timeout=timeout) as response:  # noqa: S310
            status, body = response.status, response.read(4096)
    except urllib.error.HTTPError as refused:
        status = refused.code
    except Exception as error:
        return Result(
            name,
            "unreachable",
            f"{host}:{port} accepted a connection and did not answer HTTP: {type(error).__name__}",
            "check that what is listening on that port is slurmrestd",
        )

    if status == 401:
        # The state a correctly built REGIN is in: slurmrestd running with
        # AuthType=auth/jwt, refusing an unauthenticated caller. Whether our
        # token works is a question this deliberately does not ask.
        return Result(
            name,
            "ok",
            f"slurmrestd is answering on {host}:{port} and wants a token, which is correct",
            "",
        )

    if status == 404:
        return Result(
            name,
            "unreachable",
            f"{host}:{port} answers HTTP and has no {_SLURMRESTD_VERSION} ping endpoint",
            f"check slurmrestd serves {_SLURMRESTD_VERSION}; the driver speaks that version",
        )

    if status != 200:
        return Result(
            name,
            "unreachable",
            f"{host}:{port} answered {status}, which slurmrestd does not for a ping",
            "check that what is listening on that port is slurmrestd",
        )

    if b"meta" not in body and b"pings" not in body:
        return Result(
            name,
            "unreachable",
            f"{host}:{port} answered 200 and the body is not a slurmrestd ping",
            "check that what is listening on that port is slurmrestd",
        )

    if not token:
        return Result(
            name,
            "ok",
            f"slurmrestd is answering on {host}:{port}; partitions not checked, no token",
            "set DRAUPNIR_SCHEDULER_TOKEN in secrets.env to check them too",
        )

    return _check_partitions(url, token=token, user_name=user_name, timeout=timeout, name=name)


def _check_partitions(url: str, *, token: str, user_name: str, timeout: int, name: str) -> Result:
    """Ask the scheduler what partitions it has, and compare."""
    import json as _json
    import urllib.error
    import urllib.request

    from draupnir.motsognir.placement import UnknownPartitionError, verify_partitions

    request = urllib.request.Request(  # noqa: S310
        f"{url.rstrip('/')}/slurm/{_SLURMRESTD_VERSION}/partitions",
        headers={
            "X-SLURM-USER-TOKEN": token,
            **({"X-SLURM-USER-NAME": user_name} if user_name else {}),
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            payload = _json.loads(response.read())
    except urllib.error.HTTPError as refused:
        if refused.code == 401:
            return Result(
                name,
                "auth-refused",
                "slurmrestd refused the token",
                "check DRAUPNIR_SCHEDULER_TOKEN; a Slurm token expires, so it may need reissuing",
            )
        return Result(
            name,
            "unreachable",
            f"slurmrestd answered {refused.code} when asked for its partitions",
            "check the token has permission to read the cluster",
        )
    except Exception as error:
        return Result(
            name, "unreachable", f"{type(error).__name__} reading partitions", "check slurmrestd"
        )

    found = [
        str(item["name"])
        for item in (payload.get("partitions") or [])
        if isinstance(item, dict) and item.get("name")
    ]
    try:
        verify_partitions(found)
    except UnknownPartitionError as refusal:
        return Result(
            name,
            "missing",
            str(refusal).replace("\n", " "),
            "declare it in slurm.conf on REGIN (VLD-INF-SINDRI-001 section 34)",
        )

    return Result(name, "ok", f"slurmrestd is answering, partitions {', '.join(found)}", "")


def check(environ: dict[str, str] | None = None) -> list[Result]:
    """Both dependencies, from the environment the container will be given."""
    source = dict(os.environ) if environ is None else environ
    return [
        check_postgresql(source.get("DRAUPNIR_DATABASE_URL_SYNC", "")),
        check_object_store(
            source.get("DRAUPNIR_OBJECT_STORE_ENDPOINT", ""),
            access_key=source.get("DRAUPNIR_OBJECT_STORE_ACCESS_KEY", ""),
            secret_key=source.get("DRAUPNIR_OBJECT_STORE_SECRET_KEY", ""),
            bucket=source.get("DRAUPNIR_OBJECT_STORE_BUCKET", "draupnir"),
            secure=source.get("DRAUPNIR_OBJECT_STORE_SECURE", "true").lower()
            in {"1", "true", "yes"},
        ),
        check_vault(source.get("DRAUPNIR_VAULT_ROOT", "")),
        check_scheduler(
            source.get("DRAUPNIR_SCHEDULER_URL", ""),
            token=source.get("DRAUPNIR_SCHEDULER_TOKEN", ""),
            user_name=source.get("DRAUPNIR_SCHEDULER_USER", ""),
        ),
    ]


def main(argv: list[str] | None = None) -> int:
    """Print one line per dependency. Non-zero when one of them will refuse us."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)

    results = check()
    for result in results:
        print(result.render())
    return 1 if any(result.failed for result in results) else 0


if __name__ == "__main__":
    sys.exit(main())
