# draupnir-local-subprocess

The reference `ScheduleDriver`. SAD 8.2 lists "local subprocess for
development" alongside Slurm and Ray; this is it.

It runs a rendered `JobPlan` as a child process on the machine the control
plane is running on. That makes it useful for development and for the
conformance suite, and useless for anything else: it has no queue, no
placement, and no notion of a second machine.

It is also the worked example a driver author copies. Everything it does is
what SAD 5.2 permits a schedule driver to do -- submit, observe, cancel -- and
nothing it does requires knowing what the job computes.

```bash
uv pip install -e plugins/local_subprocess
```

Its conformance is proved in `tests/contract/`, against the suite published in
`draupnir.interfaces.testing`.
