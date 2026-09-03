# stedi-view — the CON-A local status view

S30. Eight lines of local appliance state on the 9-inch panel attached to
DVALIN, and **nothing else**.

## What it is for

> **DESIGN DECISION U2. CON-A is not a small version of the console.**
>
> CON-A exists because the DGX Spark has no baseboard management controller,
> so it is the only console that survives a total network failure. Its entire
> value is that it depends on nothing beyond the appliance it is attached to.

That sentence is the specification. Everything below follows from it.

## What it must not do

| Forbidden | Why |
| --- | --- |
| Call the DRAUPNIR API | The API is one of the things it exists to outlive. A view that reads the API shows nothing during the outage it was bought for. |
| Import from `web/` | Sharing a module with the console is how an API dependency arrives later, by someone adding a fetch to a helper that both use. There is no shared code path, so there is nothing to accidentally share. |
| Require a network | The panel is driven directly by DVALIN over the local framebuffer. If the switch is dead, this still renders. |
| Accept input | It is read only. There is no control on it, so there is no action that could fail. |

The prompt states the same three constraints: *"CON-A view is read-only, reads
the local appliance only, and works when the API is unreachable. That is its
entire purpose."*

## What it shows

Eight lines, in this order, because it is the order someone standing in front
of a hot rack asks them in:

1. **GPU** — utilisation and memory, from `nvidia-smi`
2. **Throttle** — thermal or power capping, from `nvidia-smi`
3. **Fabric** — link state of the ConnectX interface, from `/sys/class/net`
4. **Ring** — reachability of the two ring neighbours, by ICMP on the local segment
5. **Run** — what this appliance is training, from the local scheduler spool
6. **Vault** — whether the local secret agent is responding, over a unix socket
7. **Scheduler** — whether the local `slurmd` is responding
8. **API** — whether the control plane answers

The last two are **expected to read unreachable during an outage, and the view
continues to function**. That is the point. Line 8 is the only line that names
the API at all, and it is a status line rather than a data source: it says
whether the API answered, and nothing on the panel depends on the answer.

## Running it

```bash
python -m stedi_view
```

Renders once and exits, for a cron or a systemd timer writing to the panel's
framebuffer console.

```bash
python -m stedi_view --watch
```

Redraws every two seconds. This is how the panel is normally driven.

```bash
python -m stedi_view --json
```

For a test or a probe. Same readings, machine readable.

## Testing it

`tests/unit/test_stedi_view.py` runs it with every source failing — no
`nvidia-smi`, no `/sys`, no sockets, no API — and asserts that it still renders
eight lines and exits zero. That is the only test that matters here: the view
is bought for the case where everything else is broken, so the case where
everything else is broken is the case that is tested.

There is also a test that asserts the package imports no HTTP client and
nothing from `draupnir`. It reads the source rather than trusting the imports
to stay absent.
