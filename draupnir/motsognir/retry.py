"""Retry and backoff.

SAD 5.2 gives MOTSOGNIR "retry and backoff" and forbids it from knowing what a
job computes. So nothing here inspects a failure to decide whether it is worth
retrying beyond one distinction that is about the machine rather than the
work: a job killed for running out of memory will run out of memory again, and
retrying it immediately spends an allocation to learn nothing.

Backoff is exponential with jitter. The jitter is not decoration. Fifty six
array elements that fail together -- because a filesystem went away, which is
how they usually fail together -- would otherwise retry together, and hit the
same filesystem at the same moment.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta

#: The first wait. Short enough that a transient scheduler hiccup costs
#: little, long enough that a genuinely broken node is not hammered.
BASE_DELAY = timedelta(seconds=30)

#: The ceiling. Beyond this a human should be looking at it, and a longer
#: automatic wait only delays them finding out.
MAX_DELAY = timedelta(minutes=30)

#: Proportion of the delay that is randomised, to break up a thundering herd.
JITTER = 0.25

#: Exit codes that mean "this will fail again the same way".
#:
#: 137 is SIGKILL, which on these appliances is almost always the out of
#: memory killer. Retrying a run that exhausted 141 GB of HBM produces the
#: same result an hour later, having consumed another allocation.
NOT_WORTH_RETRYING: frozenset[int] = frozenset({137})


class RetryError(Exception):
    """Raised when a retry decision cannot be made."""


@dataclass(frozen=True, slots=True)
class Decision:
    """Whether to retry, when, and why not if not."""

    retry: bool
    attempt: int
    delay: timedelta = timedelta(0)
    reason: str = ""

    def due_at(self, now: datetime) -> datetime:
        """When the retry becomes eligible."""
        if now.tzinfo is None:
            msg = "retry timestamps carry an explicit offset (SAD 11E.2)"
            raise RetryError(msg)
        return now + self.delay

    def __bool__(self) -> bool:
        """Allow `if decide(...)`."""
        return self.retry


def backoff(
    attempt: int,
    *,
    base: timedelta = BASE_DELAY,
    ceiling: timedelta = MAX_DELAY,
    jitter: float = JITTER,
    rng: random.Random | None = None,
) -> timedelta:
    """Return the wait before attempt `attempt`, exponential with jitter.

    Attempt 1 waits `base`, attempt 2 twice that, and so on to the ceiling.
    """
    if attempt < 1:
        msg = f"attempts are counted from 1, not {attempt}"
        raise RetryError(msg)

    multiplier: int = 2 ** (attempt - 1)
    scaled = min(base * multiplier, ceiling)
    if jitter <= 0:
        return scaled

    source = rng or random.SystemRandom()
    spread = scaled.total_seconds() * jitter
    # Jitter downwards only, so the ceiling stays a ceiling.
    return timedelta(seconds=scaled.total_seconds() - source.uniform(0, spread))


def decide(
    *,
    attempts: int,
    budget: int,
    exit_code: int | None = None,
    rng: random.Random | None = None,
) -> Decision:
    """Whether a failed element should be retried, and after how long.

    `attempts` is how many have already been made, so the first failure calls
    this with 1.
    """
    if attempts > budget:
        return Decision(
            retry=False,
            attempt=attempts,
            reason=(f"the retry budget of {budget} is exhausted after {attempts} attempt(s)"),
        )

    if exit_code in NOT_WORTH_RETRYING:
        return Decision(
            retry=False,
            attempt=attempts,
            reason=(
                f"exit {exit_code} is the out of memory killer. The same job will "
                "exhaust the same memory, and retrying spends an allocation to "
                "learn nothing. Reduce the batch size or the sequence length."
            ),
        )

    return Decision(
        retry=True,
        attempt=attempts + 1,
        delay=backoff(attempts, rng=rng),
        reason=f"attempt {attempts + 1} of {budget + 1}",
    )


def schedule(
    attempts: int, budget: int, now: datetime, *, rng: random.Random | None = None
) -> tuple[Decision, datetime | None]:
    """Decide, and say when. Returns `(decision, due_at)`."""
    decision = decide(attempts=attempts, budget=budget, rng=rng)
    return decision, decision.due_at(now) if decision.retry else None
