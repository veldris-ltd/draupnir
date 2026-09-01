"""MOTSOGNIR, the first of dwarves: job dispatch and placement.

Scheduler drivers, array concurrency, retry policy. SAD 5.2.

Owns: Scheduler drivers, placement policy, array concurrency, retry and backoff.
Must not: Know what a job computes.
"""
