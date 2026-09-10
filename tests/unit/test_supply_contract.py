"""The supply status file as a contract. RF-E18.

The finding was that no document said which host the USB cable goes to, which
daemon writes the file, what format it is in, or where it lives — so the one
piece of DRAUPNIR that will first run during a power cut had an interface
nobody had agreed.

The tests below are about the two halves that are this repository's: the format
is named and versioned, and a stale file is refused. That second one is the
part worth reading. A status file whose daemon died is well formed, reports
mains, reports a full battery, and is wrong in exactly the circumstance the
monitor exists for — because the last thing the daemon wrote before the power
went out was that the power was fine.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from draupnir.motsognir import supply, supply_adapters

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)

#: Captured from `apcaccess status` on a Back-UPS RS 1500, trimmed to the lines
#: an adapter reads plus enough noise to prove it ignores the rest.
APCACCESS_ON_BATTERY = """APC      : 001,036,0879
DATE     : 2026-09-08 12:00:00 +0100
HOSTNAME : alviss
UPSNAME  : forge
MODEL    : Back-UPS RS 1500
STATUS   : ONBATT
LINEV    : 000.0 Volts
LOADPCT  : 62.0 Percent
BCHARGE  : 46.0 Percent
TIMELEFT : 12.5 Minutes
MBATTCHG : 5 Percent
NUMXFERS : 3
TONBATT  : 41 Seconds
END APC  : 2026-09-08 12:00:00 +0100
"""

APCACCESS_ONLINE = """APC      : 001,036,0879
MODEL    : Back-UPS RS 1500
STATUS   : ONLINE
BCHARGE  : 100.0 Percent
TIMELEFT : 45.0 Minutes
"""

APCACCESS_LOW = """MODEL    : Back-UPS RS 1500
STATUS   : ONBATT LOWBATT
BCHARGE  : 8.0 Percent
"""

#: What NUT's `upsc` prints. The contract is this, so a site running NUT needs
#: no adapter at all.
UPSC_ON_BATTERY = """battery.charge: 46
battery.runtime: 750
device.model: Back-UPS RS 1500
driver.name: usbhid-ups
ups.status: OB
"""


def write(path: Path, text: str, *, age_seconds: float = 0.0) -> Path:
    """Write a status file with a chosen age."""
    path.write_text(text, encoding="utf-8")
    when = NOW.timestamp() - age_seconds
    os.utime(path, (when, when))
    return path


# ---------------------------------------------------------------------------
# The contract is named and versioned
# ---------------------------------------------------------------------------


def test_the_contract_has_a_name_and_a_version() -> None:
    """A site can be told what to produce by name rather than by example."""
    assert supply.SCHEMA == "draupnir/supply-status/v1"
    assert supply.SCHEMA_KEY == "draupnir.schema"


def test_a_bare_upsc_dump_is_valid() -> None:
    """The whole reason for choosing NUT's format as the contract.

    A site running NUT redirects the command it already has. A translation
    layer nobody needs is a translation layer nobody notices has stopped
    running.
    """
    reading = supply.parse_status(UPSC_ON_BATTERY, at=NOW)

    assert reading.state is supply.SupplyState.BATTERY
    assert reading.charge_percent == 46.0


def test_a_file_declaring_a_later_contract_is_refused() -> None:
    """Guessing is how a monitor concludes mains during a power cut."""
    later = f"{supply.SCHEMA_KEY}: draupnir/supply-status/v2\nups.status: OL\n"

    with pytest.raises(supply.SchemaError, match="v2"):
        supply.parse_status(later, at=NOW)


def test_a_file_declaring_this_contract_is_accepted() -> None:
    """The marker is optional, and honoured when present."""
    stamped = supply_adapters.stamp(UPSC_ON_BATTERY)

    assert supply.parse_status(stamped, at=NOW).state is supply.SupplyState.BATTERY


def test_stamping_is_idempotent() -> None:
    """A site that runs the adapter twice does not get two markers."""
    once = supply_adapters.stamp(UPSC_ON_BATTERY)

    assert supply_adapters.stamp(once) == once


# ---------------------------------------------------------------------------
# Staleness. The failure that matters.
# ---------------------------------------------------------------------------


def test_a_fresh_file_is_read(tmp_path: Path) -> None:
    """The ordinary case."""
    path = write(tmp_path / "supply.status", UPSC_ON_BATTERY, age_seconds=30)

    assert supply.read_status_file(path, at=NOW).state is supply.SupplyState.BATTERY


def test_a_stale_file_is_refused_rather_than_believed(tmp_path: Path) -> None:
    """The finding underneath the finding.

    Nothing about this file is malformed. It reports mains and a full battery,
    which is exactly what the daemon last wrote before it died — and, if it
    died with the power, exactly what was true at that moment and has not been
    true since.
    """
    path = write(
        tmp_path / "supply.status", "ups.status: OL\nbattery.charge: 100\n", age_seconds=600
    )

    with pytest.raises(supply.StaleSupplyError) as raised:
        supply.read_status_file(path, at=NOW)

    assert raised.value.path == path
    assert raised.value.age_seconds == pytest.approx(600, abs=1)
    assert "reads exactly like a healthy one" in str(raised.value)


def test_the_age_limit_is_four_missed_polls(tmp_path: Path) -> None:
    """Long enough not to alarm on a slow host, short enough to be found."""
    assert supply.MAX_AGE_SECONDS == 180.0

    path = write(tmp_path / "supply.status", UPSC_ON_BATTERY, age_seconds=179)
    assert supply.read_status_file(path, at=NOW).state is supply.SupplyState.BATTERY

    write(path, UPSC_ON_BATTERY, age_seconds=181)
    with pytest.raises(supply.StaleSupplyError):
        supply.read_status_file(path, at=NOW)


def test_the_age_limit_is_adjustable_for_a_site_that_polls_differently(
    tmp_path: Path,
) -> None:
    """A site with a slower timer should raise the limit, not disable the check."""
    path = write(tmp_path / "supply.status", UPSC_ON_BATTERY, age_seconds=600)

    reading = supply.read_status_file(path, at=NOW, max_age_seconds=900)

    assert reading.state is supply.SupplyState.BATTERY


def test_a_missing_file_is_still_a_plain_supply_error(tmp_path: Path) -> None:
    """Different fault, different type. One is configuration, one is a daemon."""
    with pytest.raises(supply.SupplyError) as raised:
        supply.read_status_file(tmp_path / "absent", at=NOW)

    assert not isinstance(raised.value, supply.StaleSupplyError)


# ---------------------------------------------------------------------------
# The adapter, against captured samples
# ---------------------------------------------------------------------------


def test_the_adapter_renders_apcupsd_output_the_parser_reads(tmp_path: Path) -> None:
    """The acceptance criterion: a captured sample, end to end."""
    rendered = supply_adapters.from_apcaccess(APCACCESS_ON_BATTERY)
    path = write(tmp_path / "supply.status", rendered)

    reading = supply.read_status_file(path, at=NOW)

    assert reading.state is supply.SupplyState.BATTERY
    assert reading.charge_percent == 46.0


def test_the_adapter_renders_mains(tmp_path: Path) -> None:
    """`ONLINE` is `OL`."""
    reading = supply.parse_status(supply_adapters.from_apcaccess(APCACCESS_ONLINE), at=NOW)

    assert reading.state is supply.SupplyState.MAINS
    assert reading.charge_percent == 100.0


def test_apcupsd_low_battery_carries_both_flags() -> None:
    """`LOWBATT` renders `OB LB`, not `LB` alone.

    The two words are separate in apcupsd and combine in NUT. A file carrying
    only `LB` would halt the estate without ever having recorded a transfer,
    so the forced checkpoint that the halt is supposed to follow would never
    have happened.
    """
    rendered = supply_adapters.from_apcaccess(APCACCESS_LOW)

    assert "ups.status: OB LB" in rendered
    assert supply.parse_status(rendered, at=NOW).state is supply.SupplyState.LOW_BATTERY


def test_the_adapter_ignores_the_lines_it_does_not_need() -> None:
    """Sixteen lines in, four out. A parser that needed them all would break."""
    rendered = supply_adapters.from_apcaccess(APCACCESS_ON_BATTERY)

    assert "LOADPCT" not in rendered
    assert "NUMXFERS" not in rendered


def test_the_adapter_carries_the_model_through_for_the_operator() -> None:
    """Not read by the monitor. Read by whoever opens the file to find out why."""
    rendered = supply_adapters.from_apcaccess(APCACCESS_ON_BATTERY)

    assert "device.model: Back-UPS RS 1500" in rendered


def test_a_block_with_no_status_is_refused() -> None:
    """An adapter that emitted `OL` here would invent mains from silence."""
    with pytest.raises(supply.SupplyError, match="no STATUS"):
        supply_adapters.from_apcaccess("MODEL : Back-UPS RS 1500\nBCHARGE : 100.0 Percent\n")


def test_an_unrecognised_status_word_is_refused_rather_than_guessed() -> None:
    """Reporting mains for a state nobody has read is the failure to avoid."""
    with pytest.raises(supply.SupplyError, match="none of its words is known"):
        supply_adapters.from_apcaccess("STATUS : COMMLOST\n")


def test_shutting_down_is_two_words_and_one_state() -> None:
    """Apcupsd's only multi-word status. Split naively it is two unknowns."""
    rendered = supply_adapters.from_apcaccess("STATUS : SHUTTING DOWN\nBCHARGE : 3.0 Percent\n")

    assert supply.parse_status(rendered, at=NOW).state is supply.SupplyState.LOW_BATTERY


# ---------------------------------------------------------------------------
# A lost signal changes nothing and says so
# ---------------------------------------------------------------------------


def test_a_lost_signal_is_reported_and_not_acted_on() -> None:
    """Deliberately not a drain.

    A drain on an unreadable file would stop the estate for a daemon restart,
    would outlast its cause, and would be disabled by somebody within a month.
    """
    action = supply.signal_lost(supply.SupplyError("the daemon is not running"))

    assert action.kind is supply.ActionKind.SIGNAL_LOST
    assert "the daemon is not running" in action.reason
    assert "Dispatch continues" in action.reason


def test_a_lost_signal_does_not_touch_the_monitor_state() -> None:
    """It is a report, not an observation. The monitor has seen nothing."""
    monitor = supply.SupplyMonitor()
    supply.signal_lost(supply.SupplyError("gone"))

    assert monitor.may_dispatch() is True
    assert monitor.last_state is None
