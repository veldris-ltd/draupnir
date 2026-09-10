"""The deployment guide's estate table, held against the manual. RF-E20.

The table was wrong in three rows out of five. Each error was small — a
registry that does not exist, a log collector that is not installed, an
execution model the estate does not use — and none of them would fail
anything. It is the table somebody reads at three in the morning, which is
exactly when a reference that is quietly wrong costs the most.

So the guide is now checked against a transcription of VLD-INF-SINDRI-001 §2
rather than against nothing. The limit is the same as `site_egress`': the
pipeline cannot read a controlled document, so the transcription is verified by
hand at acceptance and everything downstream of it is verified by machine.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from draupnir.gullinbursti import roster

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
DEPLOYMENT = ROOT / "docs" / "DEPLOYMENT.md"

#: Words a guide row may use that are not services: DRAUPNIR's own units, and
#: the federation registry, which is not at this forge at all.
NOT_A_SITE_SERVICE = frozenset(
    {
        "draupnir-api",
        "draupnir-worker",
        "draupnir-web",
        "megingjord",
        "training",
        "jobs",
    }
)


def estate_table() -> dict[str, str]:
    """The guide's "what runs where" rows, as `HOST -> what it runs`."""
    text = DEPLOYMENT.read_text(encoding="utf-8")
    section = text.split("### What runs where", 1)[1].split("###", 1)[0]
    rows: dict[str, str] = {}
    for line in section.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 3 or cells[0].startswith("---") or cells[0] == "Host":
            continue
        for name in re.findall(r"[A-Z][A-Za-z_]+", cells[0]):
            rows[name.upper()] = cells[1]
    return rows


# ---------------------------------------------------------------------------
# The transcription
# ---------------------------------------------------------------------------


def test_the_transcription_carries_its_provenance() -> None:
    """A transcription with no source and no date cannot be re-checked."""
    assert roster.SOURCE_DOCUMENT == "VLD-INF-SINDRI-001"
    assert roster.SOURCE_REVISION
    assert roster.TRANSCRIBED_ON


def test_the_roster_holds_the_six_machines_the_manual_designates() -> None:
    """Section 2 names six. A machine dropped in transcription is invisible."""
    assert roster.designations() == ("DVALIN", "DURIN", "DAIN", "ANDVARI", "ALVISS", "REGIN")


def test_every_service_names_where_the_manual_says_so() -> None:
    """A service with no citation is one nobody can check."""
    for item in roster.SINDRI:
        for service in item.services:
            assert service.citation, f"{item.designation}/{service.name} has no citation"


# ---------------------------------------------------------------------------
# The guide agrees with it
# ---------------------------------------------------------------------------


def test_the_guide_describes_the_machines_the_manual_designates() -> None:
    """A row for a machine that does not exist, or a missing one, both matter."""
    rows = estate_table()

    for name in ("ALVISS", "ANDVARI", "REGIN", "DVALIN", "DURIN", "DAIN"):
        assert name in rows, f"docs/DEPLOYMENT.md's estate table has no row for {name}"


def test_no_row_claims_a_service_the_manual_does_not_give_that_machine() -> None:
    """The finding, as a property.

    Checked by token rather than by prose, so the guide can word a row however
    reads best and still cannot attribute PostgreSQL to REGIN or a registry to
    ANDVARI.
    """
    rows = estate_table()
    wrong: list[str] = []

    for name, claims in rows.items():
        try:
            found = roster.machine(name)
        except KeyError:
            continue  # Veldris_NXT is not at this forge.
        permitted = found.names | NOT_A_SITE_SERVICE
        for word in re.findall(r"[a-zA-Z][a-zA-Z0-9-]{3,}", claims.lower()):
            token = word.rstrip("s")
            if token in permitted or word in permitted:
                continue
            # Only complain about a word that is some *other* machine's
            # service. An ordinary English word in a row is not a claim.
            elsewhere = {
                other.designation
                for other in roster.SINDRI
                if token in other.names and other.designation != name
            }
            if elsewhere:
                wrong.append(f"{name} is said to run {word!r}, which is {', '.join(elsewhere)}'s")

    assert not wrong, "\n".join(wrong)


def test_the_guide_does_not_give_the_estate_an_image_registry() -> None:
    """The default image reference names a host that does not resolve.

    No machine in section 2 runs a container registry and REGIN's dnsmasq has
    no record for one, so a rollout relying on the derived default fails at the
    pull with a DNS error — a long way from the thing that is wrong.
    """
    text = " ".join(DEPLOYMENT.read_text(encoding="utf-8").split())
    rows = estate_table()

    assert "registry" not in rows.get("ANDVARI", "").lower(), (
        "the estate table gives ANDVARI an image registry; nothing in the manual "
        "installs one on any machine"
    )
    assert "no image registry on the estate" in text, (
        "the guide does not warn that the derived registry hostname does not resolve"
    )
    assert "--registry" in text


def test_the_guide_does_not_claim_a_log_collector() -> None:
    """REGIN does not run Loki, whatever three documents say.

    Procedure S11 installs `prometheus prometheus-alertmanager grafana`, and
    nothing in the manual installs Loki or any other shipper.
    """
    rows = estate_table()

    assert "loki" not in rows.get("REGIN", "").lower()


def test_the_log_shipping_decision_is_recorded() -> None:
    """RF-E20's acceptance criterion: recorded, not silently dropped.

    Deleting the word "Loki" from one table would leave the estate with no log
    aggregation and no document saying so — which is worse than the wrong row,
    because at least a wrong row is falsifiable.
    """
    # Whitespace normalised: the guide is wrapped for reading, so a sentence
    # that fits on one line here is split across two there. A test that broke
    # on a reflow would be a test that discourages editing the prose.
    text = " ".join(DEPLOYMENT.read_text(encoding="utf-8").split())

    assert "Where the logs are" in text
    assert "no log collector at Sindri" in text
    assert "the decision rather than an oversight" in text
    assert "Library/Logs/draupnir" in text, "the guide does not say where to read them on a Mac"
    assert "journalctl" in text, "nor on a Linux host"


# ---------------------------------------------------------------------------
# What the manual disagrees with itself about
# ---------------------------------------------------------------------------


def test_a_service_no_procedure_installs_is_recorded_as_such() -> None:
    """Loki is declared by a role table and delivered by nothing.

    Recorded rather than quietly dropped from the roster: a reader comparing
    §2 against this would otherwise conclude the transcription was careless.
    """
    outstanding = dict(roster.declared_but_not_installed())

    assert "REGIN" in outstanding
    assert outstanding["REGIN"].name == "loki"
    assert "no procedure installs it" in outstanding["REGIN"].citation


def test_every_contradiction_says_which_half_is_followed() -> None:
    """A contradiction recorded without a decision is a decision deferred.

    These belong to the documents' author to resolve. What this repository owes
    them is to say which half it behaves as though were true, so that when the
    author picks the other one it is clear what has to change here.
    """
    assert roster.CONTRADICTIONS, "the contradictions found while reconciling are gone"

    for item in roster.CONTRADICTIONS:
        assert item.says and item.and_says, f"{item.subject} does not state both halves"
        assert item.consequence, f"{item.subject} does not say why it matters"
        assert item.followed, f"{item.subject} does not say which half this repository follows"


def test_con_a_is_driven_by_the_appliance_it_watches() -> None:
    """Decision U2, and the contradiction that would have broken it.

    Section 6.2 lists the HDMI lead as "REGIN to CON-A". CON-A exists to
    survive the network being gone, which only holds if the appliance it
    watches is the one driving it — driven from REGIN it is a second REGIN
    console and not a local view at all.
    """
    assert "con-a" in roster.machine("DVALIN").names
    assert "con-a" not in roster.machine("REGIN").names

    (found,) = [item for item in roster.CONTRADICTIONS if "CON-A" in item.subject]
    assert "DVALIN" in found.followed
