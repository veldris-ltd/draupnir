# DRAUPNIR operator runbook

For the operator on shift at Sindri. Every degraded mode in SAD 11.2 has a
section, in the order the table lists them, and every section answers the same
four questions: how you find out, what the system already did, what you do, and
how you know it is over.

Two conventions run through all of it.

**The ledger is the state.** Nothing here asks you to correct the run registry.
`run` is a projection of the chain, rebuilt by
`draupnir.core.domain.projector`; a row you edit by hand is discarded by the
next rebuild, silently. If the registry looks wrong, rebuild it and read the
chain.

**A refusal is information.** Every refusal in this system names what was
refused and why. Before working around one, read it — several of the procedures
below exist because a previous operator's workaround was the incident.

Each section ends with the test that demonstrates the behaviour, so you can see
what the system is supposed to do before you are watching it do it:

```bash
make test-degraded          # every row of SAD 11.2, faults injected for real
```

---

## Quick reference

| Symptom | Section | Release blocked? | Training continues? |
|---|---|---|---|
| Console unreachable, API 502 | [1](#1-control-plane-restarts) | during the restart | yes |
| Runs stuck in QUEUED, nothing dispatching | [2](#2-slurm-controller-on-regin-unavailable) | no | running jobs, yes |
| Array running two at a time, ring runs refused | [3](#3-one-appliance-lost) | no | yes, at reduced concurrency |
| "vault is not mounted", new runs refuse to plan | [4](#4-hodd-vault-unavailable) | yes | running jobs, yes |
| API returns 503 on every `/v1` path | [5](#5-postgresql-unavailable) | yes | yes |
| Chain verification alarm | [6](#6-ledger-chain-verification-fails) | **yes, read only** | yes |
| Anchor queue growing, release refused | [7](#7-wide-area-network-to-megingjord-lost) | yes | yes |
| Divergence alarm at both ends | [8](#8-chain-divergence-detected-at-anchor) | **yes, read only** | stop and investigate |
| Supply on battery | [9](#9-mains-loss-uninterruptible-supply-on-battery) | yes | checkpointing, then halting |

---

## 1. Control plane restarts

**How you find out.** The console stops loading and `/healthz` does not answer.
Runs on the appliances keep going; nothing on the estate notices.

**What the system already did.** Nothing was lost. Every state change was
written to the ledger inside the transaction that made it, so a control plane
that stopped between two transitions stopped after one and before the other,
never in the middle of one.

**What you do.**

```bash
make api                       # or: systemctl start draupnir-api
curl -s localhost:8000/healthz
```

The process rebuilds its view from the chain on start. There is nothing to
replay by hand and nothing to reconcile.

**How you know it is over.** `/healthz` answers, `/readyz` reports every
dependency true, and the run board shows the same states it showed before.
Service is expected within 30 seconds of the process starting (AC-N6).

**If the registry looks behind the chain:**

```bash
python tasks.py rebuild-projection --site sindri
```

That discards the table and replays sequence 1 onward. It is idempotent — the
fold is pure and the write is a full replacement — and it is the same operation
the tests use to prove the registry is derived. Rebuild it this way rather than
by hand; a row you write into `run` is discarded by the next rebuild, silently.

**Demonstrated by** `test_killing_the_control_plane_mid_run_loses_no_state` and
`test_state_is_reconstructed_from_the_ledger_rather_than_from_memory`. The
first kills a real API process with SIGKILL, mid-run.

---

## 2. Slurm controller on REGIN unavailable

**How you find out.** Queued runs stop starting. The dispatcher logs
`sbatch is not on the path` or a non-zero exit from `squeue`.

**What the system already did.** Dispatch suspended. Queued runs stayed
`QUEUED` — they were not marked failed, because nothing about them failed.
Jobs already running on the appliances are unaffected: Slurm's controller is
not in their data path.

**What you do.**

1. Confirm it is the controller and not the network: `ssh regin systemctl
   status slurmctld`.
2. Restore REGIN. Nothing in DRAUPNIR needs restarting.
3. Dispatch resumes on its own. Do **not** requeue anything: a run that is
   still `QUEUED` has not lost its place, and resubmitting it creates a second
   run with the same identity.

**How you know it is over.** `sinfo` answers, and the oldest `QUEUED` run
moves to `TRAINING` within one dispatch interval.

**Demonstrated by** `test_dispatch_suspends_when_the_scheduler_cannot_be_reached`
and `test_a_queued_run_stays_queued_when_dispatch_suspends`.

---

## 3. One appliance lost

**How you find out.** CON-B dashboard 1 shows an appliance down, or a ring run
is refused with `DegradedRingError` naming the appliance.

**What the system already did.** MOTSOGNIR reduced array concurrency from three
to two automatically — one element per available appliance, which is what the
cap has always been. Ring-partition runs refuse to plan, because a ring job
needs every appliance and a ring of two is a different job.

**What you do.** Follow VLD-INF-SINDRI-001 section 49.1 for the appliance
itself. On the DRAUPNIR side there is nothing to change: the concurrency is
derived from what is up, so it goes back to three when the appliance does.

Do **not** lower the specification's `nodes` to get a ring run through. A
substrate trained on two appliances is not the substrate the specification
describes, and the run identity will say it is.

**How you know it is over.** `plan()` for the `ring` partition stops raising,
and array concurrency reports three.

**Demonstrated by** `test_losing_an_appliance_reduces_array_concurrency_to_two`
and `test_a_ring_run_refuses_to_plan_on_a_degraded_estate`.

---

## 4. HODD vault unavailable

**How you find out.** `VaultUnavailableError: the vault at /mnt/hodd is not
mounted`. New runs refuse to plan. Capacity reads refuse rather than answering.

**What the system already did.** It refused. This is worth dwelling on, because
the behaviour was wrong until the fault was injected for real: `put` creates
the artefact's parent directories, which is correct inside a mounted vault and
catastrophic outside one. With the mount gone, `mkdir(parents=True)` recreated
the mount point on the control plane's local disk, and a run would then have
trained and staged its weights somewhere nobody backs up, nobody hashes and
nobody looks — and the vault reappearing later would have hidden the evidence.
Every store operation now checks the root first.

Jobs already running and writing to **local scratch** continue. They stage on
recovery.

**What you do.**

1. `mount | grep hodd`, then restore the NFS mount.
2. Stage what the running jobs wrote to local scratch, into the vault.
   **This is a manual step today.** SAD 11.2 says "restore NFS, run
   reconciliation"; the reconciliation command is NOT BUILT, and it is recorded
   as such in `docs/acceptance/imhotep-reconciliation.md`. Until it exists,
   copy each job's scratch output into the vault and ingest it through HODD,
   which hashes what arrives.
3. Re-hash before believing anything. An artefact is registered by the digest
   of the bytes that arrived, never by what the job said it wrote (AC-S8).

**How you know it is over.** `free_bytes()` answers, a `stat` on a known
artefact returns `exists`, and a dry run plans.

**Do not** create the mount point by hand to "unblock" planning. An empty
directory where the vault should be is the failure mode this refusal exists to
prevent.

**Demonstrated by**
`test_an_unmounted_vault_refuses_to_resolve_rather_than_inventing_a_path`,
which removes the directory and then asserts that a write is refused and that
the vault was not recreated.

---

## 5. PostgreSQL unavailable

**How you find out.** Every `/v1` path returns 503 with a problem document.
`/healthz` still answers — liveness touches no dependency by design — and
`/readyz` reports the database false.

**What the system already did.** Nothing was lost, and nothing was invented.
The API did not refuse to start: refusing would take the readiness probe down
with the database and leave you with nothing to read. Executors continue;
they do not talk to PostgreSQL.

**What you do.**

1. Restore PostgreSQL.
2. Replay is automatic — the ledger is on disk in the database, and there is
   nothing buffered in the control plane to flush.
3. Verify the chain before letting a release through:
   ```bash
   python tasks.py verify-chain
   ```

**How you know it is over.** `/readyz` reports every check true, and a run list
returns rows.

**Demonstrated by**
`test_the_api_reports_degraded_readiness_when_the_database_is_gone`, which
starts a real API process against a port nothing is listening on.

---

## 6. Ledger chain verification fails

**This is the serious one.** Everything else on this page is an outage. This is
a statement that the audit record has been altered.

**How you find out.** The hourly verification alarms, naming a sequence number.

**What the system already did.** Entered read-only mode. No release is
possible. Training continues, because stopping it would destroy work without
protecting anything.

**What you do — in this order.**

1. **Do not** append anything else to the chain. Do not "fix" the row.
2. Record the divergent sequence number and the entry hash at it:
   ```bash
   python tasks.py verify-chain --report
   ```
3. Take a copy of `ledger_entry` for the affected site before anything else
   touches it.
4. Compare the chain head against the last countersigned anchor at MEGINGJORD.
   The anchor is the independent witness: entries at or below the anchored
   sequence have a hash the federation holds, so the anchor tells you whether
   the alteration is before or after the last anchor.
5. Escalate. This is an integrity incident, not an operations one.

**Why the trigger did not stop it.** `ledger_entry` refuses `UPDATE`, `DELETE`
and `TRUNCATE` by trigger — but a role with `ALTER TABLE` can disable the
trigger, and that is exactly what someone with database access would do. The
chain hash is the control that survives that; the trigger is what stops an
accident.

**How you know it is over.** It is not over until an investigation says so. Do
not clear read-only mode to get a release out.

**Demonstrated by**
`test_editing_a_ledger_row_in_postgresql_is_detected_by_verification`, which
disables the trigger, rewrites a row, re-enables the trigger, and checks that
verification names the exact sequence number.

---

## 7. Wide area network to MEGINGJORD lost

**How you find out.** The anchor queue depth grows. A release attempt is
refused, naming the queue depth and the latest countersigned sequence.

**What the system already did.** Training, evaluation and merge continue —
Decision S8, and the whole reason the forge holds its own policy copy.
Anchoring queued, in order. Release is blocked, because a release the
federation cannot tie to a countersigned anchor is a claim nobody can check.

**What you do.**

1. Check the WireGuard tunnel first: `wg show`.
2. Restore the link. Nothing needs restarting.
3. The agent drains the queue in order on reconnect. It stops at the first
   rejection and keeps the rest queued — a head after a rejected one cannot be
   anchored without leaving a hole.

**How you know it is over.** Queue depth returns to zero, the anchored sequence
catches up to the chain head, and the release action becomes available in the
console with the reason text gone.

**A forge may run for 72 hours like this** with no degradation other than
blocked release (AC-N12). You do not need to stop work.

**Demonstrated by**
`test_severing_the_federation_link_queues_anchors_and_blocks_release` and
`test_restoring_the_link_drains_the_queued_anchors_in_order`.

---

## 8. Chain divergence detected at anchor

**How you find out.** Both the forge and the federation alarm. The forge goes
read-only.

**What the system already did.** Read-only mode, at both ends. Read-only is a
stronger state than link-down: restoring the link does **not** lift it, and the
code will not let it.

**What you do.** Section 6, from step 1. A divergence at anchor means the
forge's chain and the federation's record of it disagree, which is either an
alteration at the forge or a fault in what was submitted. Both are integrity
incidents.

**How you know it is over.** An investigation clears it. There is no operator
command that does.

**Demonstrated by** `test_a_forge_that_finds_a_divergence_goes_read_only`,
which also checks that `restore()` does not lift it.

---

## 9. Mains loss, uninterruptible supply on battery

**Note on fitment.** SAD 11.3 records the supply as USB-signalled "once
fitted". It is not fitted. DRAUPNIR reads the status file the supply's daemon
maintains — the block `upsc` prints — so the half of this that is DRAUPNIR's
works today and the half that is the estate's arrives with the hardware.

**How you find out.** The alarm on transfer to battery. In the log:
`CHECKPOINT` lines, one per running job, then `DRAIN`.

**What the system already did.**

1. Forced an immediate checkpoint on **every** running job. Once, at the
   transfer — not once per poll, because a monitor racing its own battery to
   write checkpoints is worse than none.
2. Drained the queue. Dispatch stopped; running work was left alone, because a
   job that has already consumed its allocation is work you lose by killing it
   and a job that has not started will not finish before the battery does.
3. At the low-battery threshold — the supply's own `LB` flag, or 20 per cent,
   whichever comes first — halted cleanly.

**What you do.**

1. Nothing, while the battery lasts. The sequence above is the right one and
   interrupting it costs work.
2. Restore mains.
3. On `RESUME`, dispatch restarts and running jobs resume **from the forced
   checkpoint**, not from the last periodic one.

**How you know it is over.** `may_dispatch()` reports true and the log carries
a `RESUME` line naming the restoration.

**If the status file cannot be read**, the monitor raises rather than assuming
mains. A monitor that assumed mains when it could not tell would be silent
through the one event it exists for.

**Demonstrated by**
`test_pulling_the_mains_forces_a_checkpoint_then_drains_then_halts`,
`test_restoring_mains_resumes_dispatch_from_the_forced_checkpoint` and
`test_a_supply_that_cannot_be_read_is_an_alarm_not_an_assumption`.

---

## Running the procedure

The M1 to M10 sequence is one command. It asks nothing between steps.

```bash
make procedure                                  # GBR, against the dev stack
python scripts/procedure.py --jurisdiction IRL  # any Tier A jurisdiction
```

It stops at the first refusal, prints the refusal's own words, and rolls back —
so a procedure that stopped at M6 leaves the chain exactly as M5 left it. The
evidence is written to `docs/acceptance/evidence/procedure-m1-m10.json`.

## What to read when something is wrong

| Question | Where |
|---|---|
| What state is this run in, and why? | The ledger. `GET /v1/audit/ledger?subject=<run>` |
| Is the chain intact? | `python tasks.py verify-chain` |
| Which release does this artefact belong to? | `GET /v1/models/{artefact}/lineage` |
| Why was this refused? | The problem document's `detail`, and the correlation id |
| Who approved this, and were they the submitter? | The model card's `soleApproverException` |
| What does this gate mean? | `draupnir/gleipnir/gates.py`, `SUITE` |
