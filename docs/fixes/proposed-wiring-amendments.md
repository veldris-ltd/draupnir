# Change proposal: the supply signal, end to end

**For** VLD-WIR-SINDRI-001 Rev 2.0 → Rev 2.1
**Raised by** RF-E18 of [remedial-fixes-estate.md](remedial-fixes-estate.md)
**Status** Proposed. Not applied — VLD-WIR-SINDRI-001 is a controlled document
and this is a draft for its author to accept, amend or reject.

---

## Why

Gap G1 records a 1500 VA line-interactive UPS, on order, and §7.4 says: "The
supply signals over USB. DRAUPNIR forces an immediate checkpoint on every
running job and drains at the low battery threshold."

Nothing on either side says which host the USB cable goes to, which daemon
writes the status file, what format it is in, or where it lives. DRAUPNIR's
half is now specified — a versioned contract, an adapter, and a read-only bind
mount into the worker container — and the repository half is done. What
follows is the estate's half.

**It is worth settling before the hardware arrives**, because the first time
this runs will be during a power cut, and a power cut is a bad time to discover
that the machine holding the USB cable was not on the protected circuit.

---

## The finding that changes the recommendation

The estate register suggested the UPS "feeds PDU-A and PDU-B". The cable
schedule at §5.1 says otherwise:

| ID | From | To | Note |
|---|---|---|---|
| P-01 | Wall outlet A | PDU-A, STEDI U1 | "UPS inserts here when fitted, gap G1" |
| P-02 | Wall outlet B | PDU-B, BELGR U1 | |
| P-03 | Wall outlet B | PDU-C, TANGIR U1 | |

**The UPS inserts at P-01 only.** So on mains loss, as specified:

| PDU | Holds | On battery? |
|---|---|---|
| PDU-A | DVALIN, DURIN, DAIN | **yes** |
| PDU-B | ANDVARI, ALVISS, the 200 W GaN hub → REGIN, NAIN, NORI, all three fans | no |
| PDU-C | CON-B, the GDSTIME 80 mm and 120 mm fans | no |

The three appliances survive. Everything they need does not:

- **ALVISS dies**, so DRAUPNIR is gone. The control plane that §7.4 says will
  force the checkpoint is not powered when the signal arrives.
- **ANDVARI dies**, so the `/forge/vault` NFS export is gone. A job writing a
  checkpoint to `/forge/vault/models/adapters/…` blocks in uninterruptible
  sleep on a hard mount. There is nowhere for the forced checkpoint to go.
- **REGIN dies** (GaN hub P-10, on PDU-B), so there is no scheduler to command
  the checkpoint, no DNS and no clock.
- **NAIN and NORI die** (P-11, P-12), so the three surviving appliances cannot
  reach each other or anything else.
- **Every fan dies** — FAN-A1, FAN-B1 and FAN-B2 on the GaN hub, and both
  GDSTIME sets on PDU-C.

So the UPS as placed keeps alive exactly the three machines that can do nothing
alone, kills the one machine that is supposed to act, removes the destination
the checkpoint would be written to, and leaves 720 W of compute running at full
training load with no extraction whatsoever.

This also inverts §7.1's rule that "cooling starts first and stops last".

---

## Proposed amendment 1 — the UPS feeds PDU-B, and P-01 is not the insertion point

**If only one PDU can be protected, it must be PDU-B.** The reasoning is about
what each choice can accomplish rather than what it costs:

- **PDU-B protected** — the control plane, the vault, the scheduler, DNS and
  the network all stay up. The appliances drop instantly and each running job
  loses the work since its last periodic checkpoint. But the estate shuts down
  in a consistent state, the ledger is intact, the NFS export was never yanked
  from under a writer, and recovery is the ordinary cold start.
- **PDU-A protected** (as specified) — the appliances stay up with nothing to
  talk to, nowhere to write and no cooling, then lose their work anyway when
  the battery ends. Nothing is gained and the shutdown is dirtier.

Proposed change to §5.1:

| ID | From | To | Type | Notes |
|---|---|---|---|---|
| P-02 | Wall outlet B | **UPS**, then PDU-B, BELGR U1 | UK 13 A, 1.8 m | Ring segment 2. **The UPS inserts here, not at P-01.** PDU-B holds the control plane, the vault, the scheduler and the network — everything a graceful stop needs |

and to §5.1's P-01 note: strike "UPS inserts here when fitted, gap G1".

---

## Proposed amendment 2 — what a 1500 VA unit can actually hold

Worth stating with the placement, because the obvious improvement — protect
PDU-A *and* PDU-B, so the forced checkpoint can actually be written — does not
fit the unit on order.

Rough figures, for the author to replace with measured ones at commissioning:

| Load | Draw |
|---|---|
| PDU-A: three appliances at training load | ~720 W |
| PDU-B: ANDVARI, ALVISS, GaN hub | ~300 W |
| Both | ~1020 W |

A 1500 VA line-interactive unit is typically rated near 900 W real power. So
**both PDUs at training load exceeds it**, and the honest options are:

1. **PDU-B only, on the unit on order.** ~300 W gives a long window — many
   minutes — which is ample for DRAUPNIR to drain, halt and shut down cleanly.
   The in-flight training step is lost.
2. **Both PDUs, on a larger unit** (≈2200 VA / 1400 W). This is the only option
   in which §7.4's forced checkpoint can actually complete, because it is the
   only one where the appliance, the vault and the control plane are
   simultaneously alive.
3. **Both PDUs on the unit on order, accepting a short window.** Roughly a
   minute at best. Adequate for a LoRA adapter checkpoint; not adequate for a
   MIDGARD-CORE full-state checkpoint on the `ring` partition, which is the
   case the estate exists for.

**Option 2 is the one that makes §7.4 true as written.** Options 1 and 3 both
require §7.4 to be amended, because under either of them "DRAUPNIR forces an
immediate checkpoint on every running job" is not what happens.

---

## Proposed amendment 3 — §7.4, so it describes what will happen

If option 1 is chosen, the "Mains loss, UPS fitted" row becomes:

> The supply signals over USB to ALVISS. DRAUPNIR stops dispatching, records
> the transfer to the ledger, and halts the control plane cleanly at the low
> battery threshold. **The appliances are not on the protected circuit and stop
> at once**, so running jobs resume from their last periodic checkpoint rather
> than from a forced one. If mains returns before the halt threshold, dispatch
> resumes and no action is required.

If option 2 is chosen, the row stands as written, and gains:

> Cooling is not on the protected circuit. The drain threshold must therefore
> be set so the appliances stop before the room heats — see amendment 4.

---

## Proposed amendment 4 — the cooling question, and §7.1's ordering rule

§7.1 states that "cooling starts first and stops last, so the installation is
never under load without extraction". Under any option above, mains loss
inverts it: the fans are on PDU-C and the GaN hub, and neither is protected.

Two ways to settle it, and the choice depends on amendment 2:

**If option 1 (PDU-B only):** nothing further is needed, and §7.1 gains a
sentence saying why:

> This ordering governs deliberate starts and stops. On mains loss the
> appliances stop before the fans do, because the appliances are not on the
> protected circuit — so the installation is never under load without
> extraction in that case either, by a different mechanism.

**If option 2 (both PDUs):** the appliances run at full load with no
extraction for the duration of the battery, and this must be bounded. Either:

- **PDU-C joins the protected circuit.** The GDSTIME sets are a small load and
  this is the clean answer, at the cost of a third outlet on the UPS.
- **Or the drain threshold is set by thermal headroom rather than by battery
  charge.** DRAUPNIR halts at 20 per cent by default
  (`supply.LOW_BATTERY_PERCENT`); with no extraction the binding constraint is
  the compute plane's temperature rise, not the battery. §7.1 then gains an
  explicit exception naming the measured safe interval.

The first is recommended. A threshold derived from a thermal model is a number
nobody will re-derive when the room changes.

---

## Proposed amendment 5 — gap G1 names the host, the daemon and the file

Gap G1 currently records the unit and its purpose. Proposed additional text:

> **Signal path.** The UPS USB cable connects to **ALVISS**. ALVISS is on
> PDU-B, runs the DRAUPNIR worker, and is the host that must act on the signal;
> a cable to any other host would require the signal to be forwarded over a
> network that mains loss is about to remove.
>
> **Daemon.** NUT (`nut`), driver `usbhid-ups`. Portable across macOS and
> Linux, and it is the format DRAUPNIR reads natively.
>
> **Status file.** `/opt/homebrew/var/draupnir/supply.status` on ALVISS,
> written every 60 s by a launchd timer:
>
> ```
> upsc forge@localhost > /opt/homebrew/var/draupnir/supply.status.tmp \
>   && mv /opt/homebrew/var/draupnir/supply.status.tmp \
>         /opt/homebrew/var/draupnir/supply.status
> ```
>
> Written to a temporary file and moved, so DRAUPNIR never reads a half-written
> block. `mv` within one filesystem is atomic; a direct redirect is not.
>
> **Contract.** `draupnir/supply-status/v1`, which is the block `upsc` prints.
> DRAUPNIR reads `ups.status` and `battery.charge` and ignores the rest.
> DRAUPNIR **refuses a file older than 180 seconds** — a status file whose
> daemon has died reports mains and a full battery, which is well formed,
> plausible and wrong in exactly the circumstance the signal exists for. A site
> that polls more slowly than 60 s must raise that limit rather than remove it.
>
> **A site running `apcupsd` instead** pipes `apcaccess status` through
> `draupnir.motsognir.supply_adapters.from_apcaccess`, which renders it into
> the same contract.
>
> **Configuration.** `DRAUPNIR_WORKER_SUPPLY_STATUS` in
> `~/.config/draupnir/draupnir.env` on ALVISS. `deploy/install.sh` writes it and
> `draupnir-run.sh` bind-mounts the file read-only into the worker container.
> Empty means no supply is fitted, which is the state today and which starts
> the worker unchanged.

---

## Proposed amendment 6 — a post-fitting check

§8 runs seven post-start checks and none of them would notice that the supply
signal is not arriving. Proposed eighth row, applicable once G1 closes:

| # | Check | Command | Pass |
|---|---|---|---|
| 8 | Supply signal | On ALVISS: `upsc forge@localhost` and then, within 60 s, `stat -f %m /opt/homebrew/var/draupnir/supply.status` | The command reports `ups.status: OL`, and the file's modification time is within 60 s of now |

The second half is the one worth having. A status file that exists is not a
status file that is being written, and the difference is invisible until the
power goes out.
