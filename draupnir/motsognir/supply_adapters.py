"""Rendering a supply daemon's own output into the status contract. RF-E18.

`supply.SCHEMA` v1 is the block `upsc` prints, so a site running NUT needs
nothing from this module: `upsc forge@localhost > /run/draupnir/supply.status`
on a timer is the whole integration. That is the point of choosing NUT's format
as the contract rather than inventing one -- the recommended path has no moving
part, and a translation layer nobody needs is a translation layer nobody
notices has stopped running.

`apcupsd` is the other daemon a site plausibly already has, and it prints
something else. This converts it, so the estate is not obliged to hand-write
DRAUPNIR's format from a shell script.

**Every adapter here is pure text in, text out.** No daemon is imported, no
device is opened, and nothing is scheduled. The site's own timer runs the
daemon's command and pipes it through; what this owns is the translation, which
is the part worth testing against a captured sample.
"""

from __future__ import annotations

from typing import Final

from draupnir.motsognir.supply import SCHEMA, SCHEMA_KEY, SupplyError

#: apcupsd's status words, mapped to the NUT flags the contract is written in.
#:
#: `ONBATT` and `LOWBATT` are separate words in apcupsd and combine in NUT, so
#: a low battery on apcupsd renders `OB LB` -- both flags, because the parser
#: reads `LB` as the halt signal and `OB` as the transfer, and a file carrying
#: only the first would halt without ever having recorded a transfer.
_APC_STATUS: Final[dict[str, tuple[str, ...]]] = {
    "ONLINE": ("OL",),
    "ONBATT": ("OB",),
    "LOWBATT": ("OB", "LB"),
    "CAL": ("OL", "CAL"),
    "TRIM": ("OL", "TRIM"),
    "BOOST": ("OL", "BOOST"),
    "OVERLOAD": ("OL", "OVER"),
    "REPLACEBATT": ("OL", "RB"),
    "SHUTTING DOWN": ("OB", "LB"),
}


def _fields(text: str, separator: str) -> dict[str, str]:
    """Split a `key <sep> value` block, ignoring anything that is not one."""
    found: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition(separator)
        if sep:
            found[key.strip()] = value.strip()
    return found


def from_apcaccess(text: str) -> str:
    """Render `apcaccess status` output into the v1 contract.

    apcupsd prints `KEY  : value`, with the status as one or more words and the
    charge as `100.0 Percent`. Both need unpicking; neither is difficult, and
    the value of doing it here rather than in a shell one-liner at the site is
    that it can be tested against a captured sample.
    """
    fields = _fields(text, ":")

    status = fields.get("STATUS", "").strip()
    if not status:
        msg = (
            "the apcaccess block carries no STATUS line, so nothing can be concluded "
            "from it. An adapter that emitted `OL` here would report mains from a "
            "daemon that said nothing at all."
        )
        raise SupplyError(msg)

    flags: list[str] = []
    for word in _split_status(status):
        for flag in _APC_STATUS.get(word, ()):
            if flag not in flags:
                flags.append(flag)
    if not flags:
        msg = (
            f"apcaccess reported STATUS {status!r} and none of its words is known here. "
            "Guessing would mean reporting mains for a state nobody has read."
        )
        raise SupplyError(msg)

    lines = [f"{SCHEMA_KEY}: {SCHEMA}", f"ups.status: {' '.join(flags)}"]

    charge = fields.get("BCHARGE", "")
    if charge:
        lines.append(f"battery.charge: {_number(charge, 'BCHARGE')}")

    # Carried through where present. Not read by the monitor, and useful to an
    # operator reading the file to find out what the daemon actually said.
    for source, target in (("MODEL", "device.model"), ("TIMELEFT", "battery.runtime.minutes")):
        if source in fields:
            lines.append(f"{target}: {fields[source]}")

    return "\n".join(lines) + "\n"


def _split_status(status: str) -> tuple[str, ...]:
    """Split the status words. `SHUTTING DOWN` is two words and one state."""
    upper = status.upper()
    if "SHUTTING DOWN" in upper:
        rest = upper.replace("SHUTTING DOWN", " ").split()
        return ("SHUTTING DOWN", *rest)
    return tuple(upper.split())


def _number(value: str, key: str) -> str:
    """`100.0 Percent` to `100.0`. The unit is the daemon's, not the contract's."""
    head = value.split()[0] if value.split() else ""
    try:
        float(head)
    except ValueError as error:
        msg = f"apcaccess reported {key} {value!r}, which carries no number"
        raise SupplyError(msg) from error
    return head


def stamp(text: str) -> str:
    """Add the schema marker to a bare `upsc` dump.

    Optional, and worth having for the same reason the marker is: a file that
    names its contract can be refused by a later revision rather than
    misread. A site redirecting `upsc` straight to the file is valid without
    this -- absent means v1 -- so this is for a site that would rather be
    explicit.
    """
    if SCHEMA_KEY in text:
        return text
    return f"{SCHEMA_KEY}: {SCHEMA}\n{text}"


__all__ = ["from_apcaccess", "stamp"]
