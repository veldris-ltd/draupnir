"""BRISINGAMEN, the necklace four dwarves made together: reweighting and merge.

Merge method drivers, weight sweeps, release route selection. SAD 5.2.

Owns: Merge method drivers, weight sweep execution, adapter to dense export.
Must not: Decide whether a merge is acceptable. RAUN decides.

| Module | What it decides |
|---|---|
| `routes` | What a release ships as, and which formats that permits |
| `sweep` | N merge points and their results, as one comparable object |
| `merge` | What is merged into what, and the hash that identifies it |

The prohibition is load bearing. `Sweep.select` reads the verdict on a point and
refuses one that failed; nothing here forms a view about whether a number is
good. The driver is a plug-in (`plugins/brisingamen_mergekit`).
"""
