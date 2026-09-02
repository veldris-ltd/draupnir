"""HAMARR, the hammer: training executors.

Framework drivers for substrate and adapter runs. SAD 5.2.

Owns: Framework drivers, run configuration rendering, checkpoint policy, log capture.
Must not: Choose which model to train, or judge the result.

| Module | What it decides |
|---|---|
| `tiers` | Which of the fifty six a jurisdiction is, and which base it trains on |
| `checkpoints` | How often to write, so that no more than thirty minutes is lost |
| `config` | What submission settles before a driver renders anything |
| `progress` | What the run board is allowed to know about a running job |

"Must not choose which model to train" and "tier drives base selection" are
not in tension: `tiers` applies a table the programme fixed, it does not
choose. Nothing here reads a corpus or judges an output, which is RAUN's.

The framework driver is a plug-in (`plugins/hamarr_llamafactory`), so an
upgrade to LLaMA-Factory is a version bump of a distribution rather than a
change to the control plane.
"""
