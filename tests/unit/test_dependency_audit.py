"""The frontend dependency audit, and what it does with an advisory. RF-26.

`python tasks.py audit` ran `pnpm audit --audit-level high`, and two moderate
advisories -- `yaml` and `uuid` -- sat in every report below that line. Nothing
was wrong with the threshold as a judgement; what was wrong was that nobody had
made one. An advisory below a threshold is invisible, and an invisible
advisory has not been accepted, only not looked at.

So the audit now fails at moderate, names everything it does not fail on, and
accepts an advisory only through an exception that says why and expires.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

if str(REPO_ROOT) not in sys.path:  # pragma: no cover - `tasks` lives at the root
    sys.path.insert(0, str(REPO_ROOT))

import tasks  # noqa: E402 -- the repository root has to be on the path first

pytestmark = pytest.mark.unit

TODAY = date(2026, 9, 14)


def advisory(
    ghsa: str = "GHSA-48c2-rrv3-qjmp",
    package: str = "yaml",
    severity: str = "moderate",
    version: str = "2.8.1",
) -> dict[str, object]:
    """An advisory in the shape `pnpm audit --json` reports one."""
    return {
        "github_advisory_id": ghsa,
        "module_name": package,
        "severity": severity,
        "title": f"{package} is vulnerable",
        "patched_versions": ">=99.0.0",
        "findings": [{"version": version, "paths": [f". > vite@6.4.3 > {package}@{version}"]}],
    }


def report(*advisories: dict[str, object]) -> dict[str, object]:
    return {"advisories": {str(index): found for index, found in enumerate(advisories)}}


def accepting(
    ghsa: str = "GHSA-48c2-rrv3-qjmp",
    package: str = "yaml",
    expires: date = TODAY + timedelta(days=30),
    reason: str = "build-time only; never parses untrusted YAML, never shipped",
) -> tasks.AuditException:
    return tasks.AuditException(advisory=ghsa, package=package, reason=reason, expires=expires)


# ---------------------------------------------------------------------------
# The threshold
# ---------------------------------------------------------------------------


def test_a_clean_report_passes() -> None:
    assert tasks.audit_verdict(report(), exceptions=(), today=TODAY) == ([], [])


def test_a_moderate_advisory_fails_the_build() -> None:
    """The RF-26 case. At `high` this passed, and said nothing."""
    blocking, _noted = tasks.audit_verdict(report(advisory()), exceptions=(), today=TODAY)

    assert len(blocking) == 1
    assert "GHSA-48c2-rrv3-qjmp" in blocking[0]
    assert "yaml@2.8.1" in blocking[0], "the finding does not say which version is installed"


def test_the_pipeline_gates_at_moderate() -> None:
    assert tasks.AUDIT_LEVEL == "moderate"


def test_an_advisory_below_the_threshold_is_named_rather_than_hidden() -> None:
    """Below the line is not the same as unmentioned."""
    blocking, noted = tasks.audit_verdict(
        report(advisory(severity="low")), exceptions=(), today=TODAY
    )

    assert blocking == []
    assert len(noted) == 1
    assert "below the moderate threshold" in noted[0]
    assert "GHSA-48c2-rrv3-qjmp" in noted[0]


def test_a_severity_the_task_does_not_know_fails_rather_than_passes() -> None:
    blocking, _noted = tasks.audit_verdict(
        report(advisory(severity="severe")), exceptions=(), today=TODAY
    )

    assert blocking and "unrecognised severity" in blocking[0]


def test_a_run_that_produced_no_report_fails() -> None:
    """The registry unreachable, say. An audit that did not happen found nothing."""
    blocking, _noted = tasks.audit_verdict(
        {"error": {"code": "ERR_PNPM_AUDIT_BAD_RESPONSE"}}, exceptions=(), today=TODAY
    )

    assert blocking and "nothing was audited" in blocking[0]


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


def test_an_exception_in_date_accepts_the_advisory_and_says_why() -> None:
    exception = accepting()

    blocking, noted = tasks.audit_verdict(report(advisory()), exceptions=(exception,), today=TODAY)

    assert blocking == []
    assert len(noted) == 1
    assert exception.reason in noted[0], "an accepted advisory is logged without its reason"
    assert exception.expires.isoformat() in noted[0]


def test_an_expired_exception_fails_the_build() -> None:
    """The acceptance criterion. An exception that outlives its date is a threshold."""
    exception = accepting(expires=TODAY - timedelta(days=1))

    blocking, _noted = tasks.audit_verdict(report(advisory()), exceptions=(exception,), today=TODAY)

    assert len(blocking) == 1
    assert "expired" in blocking[0]
    assert exception.expires.isoformat() in blocking[0]


def test_an_exception_holds_through_its_expiry_date() -> None:
    blocking, _noted = tasks.audit_verdict(
        report(advisory()), exceptions=(accepting(expires=TODAY),), today=TODAY
    )

    assert blocking == []


def test_an_exception_cannot_be_written_to_expire_in_the_far_future() -> None:
    """Otherwise an expiry of 2099 satisfies the rule and defeats it."""
    too_far = TODAY + tasks.AUDIT_EXCEPTION_HORIZON + timedelta(days=1)

    blocking, _noted = tasks.audit_verdict(
        report(advisory()), exceptions=(accepting(expires=too_far),), today=TODAY
    )

    assert blocking and "days away" in blocking[0]


def test_an_exception_needs_a_reason() -> None:
    blocking, _noted = tasks.audit_verdict(
        report(advisory()), exceptions=(accepting(reason="  "),), today=TODAY
    )

    assert blocking and "no reason" in blocking[0]


def test_an_exception_for_another_package_excuses_nothing() -> None:
    """Matched on advisory and package, so a slip excuses nothing rather than something."""
    blocking, _noted = tasks.audit_verdict(
        report(advisory()), exceptions=(accepting(package="yamljs"),), today=TODAY
    )

    assert any("GHSA-48c2-rrv3-qjmp yaml@" in line for line in blocking)


def test_an_exception_for_an_advisory_no_longer_reported_must_be_removed() -> None:
    """Exceptions that outlive their advisory accumulate into an allow-list nobody reads."""
    blocking, _noted = tasks.audit_verdict(report(), exceptions=(accepting(),), today=TODAY)

    assert blocking and "no longer reports" in blocking[0]


def test_the_repository_exceptions_are_well_formed() -> None:
    """Structure only. Expiry is the audit stage's job, not a date bomb in the unit suite."""
    for exception in tasks.AUDIT_EXCEPTIONS:
        assert exception.advisory.startswith("GHSA-")
        assert exception.package
        assert len(exception.reason.split()) >= 8, (
            f"{exception.advisory}: say why it does not reach anything shipped"
        )


# ---------------------------------------------------------------------------
# The task
# ---------------------------------------------------------------------------


def _without_pip_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tasks, "uv", lambda: "uv")
    monkeypatch.setattr(tasks, "run", lambda *_args, **_named: 0)
    monkeypatch.setattr(tasks, "uv_run", lambda *_args, **_named: 0)


def test_the_task_fails_on_a_moderate_advisory(monkeypatch: pytest.MonkeyPatch) -> None:
    _without_pip_audit(monkeypatch)
    monkeypatch.setattr(tasks, "pnpm_audit_report", lambda: report(advisory()))

    with pytest.raises(tasks.Failure) as failed:
        tasks.audit()

    assert "GHSA-48c2-rrv3-qjmp" in str(failed.value)


def test_the_task_passes_a_clean_report(monkeypatch: pytest.MonkeyPatch) -> None:
    _without_pip_audit(monkeypatch)
    monkeypatch.setattr(tasks, "pnpm_audit_report", lambda: report())

    assert tasks.audit() == 0
