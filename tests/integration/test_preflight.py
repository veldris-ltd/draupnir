"""The dependency check tells four failures apart, against real services.

RF-E03. `install.sh` used to open a TCP socket and call that a dependency
check. At Sindri a socket answering is compatible with every one of the three
things VLD-INF-SINDRI-001 Procedure S8 leaves undone: no `draupnir` database,
no bucket, and PostgreSQL bound to localhost. All three would have passed.

These run against the same PostgreSQL and MinIO containers the rest of the
integration stage uses, because the classification is a claim about what those
servers actually do. The PostgreSQL half in particular cannot be tested any
other way: psycopg reports every connection-time failure as a bare
`OperationalError` with no SQLSTATE, so the check probes rather than reads a
code, and whether the probe lands is a property of the server.
"""

from __future__ import annotations

import pytest

from scripts.preflight import Result, check_object_store, check_postgresql

pytestmark = pytest.mark.integration


def _rewrite(
    url: str,
    *,
    password: str | None = None,
    database: str | None = None,
    port: int | None = None,
) -> str:
    """The same URL with one part replaced, for the failure cases.

    `port` is a parameter rather than a string substitution because the
    container's port is allocated at run time, so there is no literal in the
    URL to replace.
    """
    from urllib.parse import urlsplit, urlunsplit

    split = urlsplit(url.replace("postgresql+psycopg://", "postgresql://", 1))
    user = split.username or "postgres"
    secret = password if password is not None else (split.password or "")
    host = split.hostname or "127.0.0.1"
    where = port if port is not None else (split.port or 5432)
    name = database if database is not None else split.path.lstrip("/")
    return urlunsplit(("postgresql+psycopg", f"{user}:{secret}@{host}:{where}", f"/{name}", "", ""))


# ---------------------------------------------------------------------------
# PostgreSQL
# ---------------------------------------------------------------------------


def test_a_database_that_accepts_us_is_ok(postgres_url: str) -> None:
    result = check_postgresql(postgres_url)
    assert result.verdict == "ok", result
    assert not result.failed


def test_a_closed_port_is_unreachable(postgres_url: str) -> None:
    """Nothing listening. No credential can be judged, so none is offered."""
    result = check_postgresql(_rewrite(postgres_url, port=1))
    assert result.verdict == "unreachable", result
    assert result.failed


def test_a_wrong_password_is_auth_refused_not_unreachable(postgres_url: str) -> None:
    """The distinction the socket test could not make.

    Same host, same port, same open socket as the healthy case. Only the
    credential differs, and reporting this as "no answer" is what sent an
    operator to the infrastructure team for a typo in secrets.env.
    """
    result = check_postgresql(_rewrite(postgres_url, password="not-the-password"))
    assert result.verdict == "auth-refused", result
    assert "secrets.env" in result.remedy


def test_an_absent_database_is_missing_not_auth_refused(postgres_url: str) -> None:
    """Credentials good, provisioning not done. Procedure S13, once.

    This is the state a correctly built Sindri is in the first time anybody
    runs the installer: Procedure S8 creates `mlflow` and stops.
    """
    result = check_postgresql(_rewrite(postgres_url, database="draupnir"))
    assert result.verdict == "missing", result
    assert "Procedure S13" in result.remedy


def test_no_password_is_unverified_rather_than_a_failure(postgres_url: str) -> None:
    """A first commissioning has no credentials by design.

    `install.sh` creates secrets.env empty and the operator fills it in
    afterwards, so failing here would make the documented order impossible to
    follow. It has to be loud and it must not stop the install.
    """
    result = check_postgresql(_rewrite(postgres_url, password=""))
    assert result.verdict == "unverified", result
    assert not result.failed
    assert "secrets.env" in result.remedy


# ---------------------------------------------------------------------------
# Object store
# ---------------------------------------------------------------------------


@pytest.fixture
def bucket(minio: dict[str, str]) -> str:
    """A bucket that exists, so `missing` means what it says."""
    from minio import Minio

    client = Minio(
        minio["endpoint"],
        access_key=minio["access_key"],
        secret_key=minio["secret_key"],
        secure=False,
    )
    name = "draupnir-preflight"
    if not client.bucket_exists(name):
        client.make_bucket(name)
    return name


def _store(minio: dict[str, str], **overrides: object) -> Result:
    settings: dict[str, object] = {
        "access_key": minio["access_key"],
        "secret_key": minio["secret_key"],
        "bucket": "draupnir-preflight",
        "secure": False,
    }
    settings.update(overrides)
    endpoint = str(settings.pop("endpoint", minio["endpoint"]))
    return check_object_store(endpoint, **settings)  # type: ignore[arg-type]


def test_a_bucket_that_is_there_is_ok(minio: dict[str, str], bucket: str) -> None:
    result = _store(minio, bucket=bucket)
    assert result.verdict == "ok", result


def test_a_closed_object_store_port_is_unreachable(minio: dict[str, str], bucket: str) -> None:
    result = _store(minio, endpoint="127.0.0.1:1", bucket=bucket)
    assert result.verdict == "unreachable", result


def test_wrong_object_store_keys_are_auth_refused(minio: dict[str, str], bucket: str) -> None:
    result = _store(minio, secret_key="not-the-secret-key", bucket=bucket)
    assert result.verdict == "auth-refused", result


def test_an_absent_bucket_is_missing(minio: dict[str, str], bucket: str) -> None:
    """The store is up, the keys work, and nothing made the bucket.

    Procedure S8 step 7 starts MinIO and creates no bucket at all, so this is
    the state the estate is in until Procedure S13 exists.
    """
    del bucket
    result = _store(minio, bucket="no-such-bucket-here")
    assert result.verdict == "missing", result
    assert "Procedure S13" in result.remedy


def test_no_access_key_is_unverified(minio: dict[str, str], bucket: str) -> None:
    result = _store(minio, access_key="", bucket=bucket)
    assert result.verdict == "unverified", result
    assert not result.failed


# ---------------------------------------------------------------------------
# The whole check
# ---------------------------------------------------------------------------


def test_the_exit_code_distinguishes_a_refusal_from_an_unverified_check(
    postgres_url: str, minio: dict[str, str], bucket: str
) -> None:
    """`unverified` must not stop a commissioning; a refusal must.

    Asserted through `failed` rather than through the process exit code, so
    that the rule is checked per dependency rather than once for the pair.
    """
    healthy = check_postgresql(postgres_url)
    unverified = check_postgresql(_rewrite(postgres_url, password=""))
    refused = check_postgresql(_rewrite(postgres_url, password="wrong"))
    absent = check_postgresql(_rewrite(postgres_url, database="draupnir"))

    assert [r.failed for r in (healthy, unverified, refused, absent)] == [
        False,
        False,
        True,
        True,
    ]

    del minio, bucket


def test_every_failing_verdict_carries_a_remedy(postgres_url: str) -> None:
    """A verdict an operator cannot act on is a verdict that wastes a call."""
    for result in (
        check_postgresql(_rewrite(postgres_url, port=1)),
        check_postgresql(_rewrite(postgres_url, password="wrong")),
        check_postgresql(_rewrite(postgres_url, database="draupnir")),
    ):
        assert result.remedy, f"{result.verdict} has no remedy"
        assert result.detail, f"{result.verdict} has no detail"


# ---------------------------------------------------------------------------
# The scheduler. RF-E05: reachability, and not a port that merely answers.
# ---------------------------------------------------------------------------


def test_no_scheduler_configured_is_unverified() -> None:
    from scripts.preflight import check_scheduler

    result = check_scheduler("")
    assert result.verdict == "unverified", result
    assert not result.failed


def test_a_closed_scheduler_port_is_unreachable() -> None:
    from scripts.preflight import check_scheduler

    result = check_scheduler("http://127.0.0.1:1")
    assert result.verdict == "unreachable", result
    assert "Procedure S11" in result.remedy


def test_a_port_that_answers_is_not_taken_for_the_scheduler(minio: dict[str, str]) -> None:
    """The failure mode a reachability check falls into if it stops at the port.

    MinIO answers this ping perfectly happily. A check that reported "the
    scheduler is up" because something replied is a check that earns being
    ignored, and the first sign of trouble would be a run that never dispatches.
    """
    from scripts.preflight import check_scheduler

    result = check_scheduler(f"http://{minio['endpoint']}")
    assert result.verdict == "unreachable", result
    assert "slurmrestd" in result.remedy
