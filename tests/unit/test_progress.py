"""Progress reaches the UI as structure, never as scraped strings.

The requirement is "progress parsing produces structured events, never
regex-scraped strings in the UI layer". Two halves: the driver owns the
patterns, and the module folds events into a record of numbers. This tests the
second half, and asserts the boundary the first half creates -- that nothing
in the served payload is a line of executor output.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from draupnir.hamarr import progress as progress_module
from draupnir.hamarr.progress import Progress, ProgressError, Stream
from draupnir.interfaces.types import ProgressEvent, ProgressKind

START = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)


def at(seconds: float) -> datetime:
    """A stamp `seconds` after the start."""
    return START + timedelta(seconds=seconds)


def steps(
    count: int, *, total: int = 1000, every: float = 6.0
) -> list[tuple[ProgressEvent, datetime]]:
    """`count` step events, `every` seconds apart."""
    return [
        (ProgressEvent(kind=ProgressKind.STEP, step=index + 1, total=total), at(index * every))
        for index in range(count)
    ]


def test_step_events_fold_into_a_position() -> None:
    result = progress_module.fold(steps(10))

    assert result.step == 10
    assert result.total == 1000
    assert result.fraction == pytest.approx(0.01)


def test_step_time_and_eta_are_derived_not_reported() -> None:
    """AC-F4's live progress: the run board needs a number, not a log line."""
    result = progress_module.fold(steps(51, every=6.0))

    assert result.step_time == timedelta(seconds=6)
    assert result.eta == timedelta(seconds=6) * (1000 - 51)


def test_a_loss_event_records_a_windowed_history() -> None:
    events = [
        (ProgressEvent(kind=ProgressKind.LOSS, step=index, value=2.0 - index * 0.001), at(index))
        for index in range(1, 200)
    ]

    result = progress_module.fold(events)

    assert result.loss == pytest.approx(2.0 - 199 * 0.001)
    assert len(result.losses) == progress_module.LOSS_WINDOW


def test_checkpoints_are_recorded_and_the_unwritten_work_is_known() -> None:
    """The number the checkpoint policy exists to keep small."""
    events = [
        *steps(100, every=6.0),
        (
            ProgressEvent(kind=ProgressKind.CHECKPOINT, step=100, message="output/ckpt-100"),
            at(600),
        ),
        *[
            (
                ProgressEvent(kind=ProgressKind.STEP, step=100 + index, total=1000),
                at(600 + index * 6),
            )
            for index in range(1, 21)
        ],
    ]

    result = progress_module.fold(events)

    assert result.last_checkpoint is not None
    assert result.last_checkpoint.step == 100
    assert result.unwritten_steps == 20
    assert result.unwritten is not None
    assert result.unwritten < timedelta(minutes=30)


def test_a_metric_replaces_its_previous_value_rather_than_accumulating() -> None:
    events = [
        (ProgressEvent(kind=ProgressKind.METRIC, message="eval_loss", value=1.5), at(1)),
        (ProgressEvent(kind=ProgressKind.METRIC, message="eval_loss", value=1.2), at(2)),
        (ProgressEvent(kind=ProgressKind.METRIC, message="eval_accuracy", value=0.8), at(3)),
    ]

    result = progress_module.fold(events)

    assert dict(result.metrics) == {"eval_accuracy": 0.8, "eval_loss": 1.2}


def test_a_step_event_without_a_step_number_is_refused() -> None:
    with pytest.raises(ProgressError):
        progress_module.advance(Progress(), ProgressEvent(kind=ProgressKind.STEP), at=START)


def test_a_naive_timestamp_is_refused() -> None:
    """SAD 11E.2: no naive timestamps, anywhere."""
    with pytest.raises(ProgressError, match="explicit offset"):
        progress_module.advance(
            Progress(),
            ProgressEvent(kind=ProgressKind.STEP, step=1),
            at=datetime(2026, 3, 2, 9, 0),  # noqa: DTZ001
        )


def test_folding_the_same_events_twice_gives_the_same_record() -> None:
    """Progress is derived, never stored. The same property the projection has."""
    events = steps(40)

    assert progress_module.fold(events) == progress_module.fold(events)


def test_the_served_payload_is_numbers_and_names_only() -> None:
    """The boundary the requirement is about: no scraped strings downstream."""
    events = [
        *steps(20),
        (ProgressEvent(kind=ProgressKind.LOSS, step=20, value=1.1), at(120)),
        (ProgressEvent(kind=ProgressKind.CHECKPOINT, step=20, message="output/ckpt"), at(121)),
        (ProgressEvent(kind=ProgressKind.METRIC, message="eval_loss", value=0.9), at(122)),
    ]

    payload = progress_module.fold(events).as_payload

    assert payload["step"] == 20
    assert isinstance(payload["stepTimeSeconds"], float)
    assert isinstance(payload["etaSeconds"], float)
    # Every value is a number, a name, a timestamp or a container of those.
    # `warnings` is the one place executor text appears, and it is empty here.
    assert payload["warnings"] == []
    assert payload["metrics"] == {"eval_loss": 0.9}


def test_a_warning_is_kept_verbatim_and_kept_apart() -> None:
    """A warning's value is its wording, so it is quoted -- and labelled."""
    result = progress_module.advance(
        Progress(),
        ProgressEvent(kind=ProgressKind.WARNING, message="gradient overflow, skipping step"),
        at=START,
    )

    assert result.warnings == ("gradient overflow, skipping step",)
    assert result.as_payload["warnings"] == ["gradient overflow, skipping step"]


def test_a_stream_folds_incrementally_to_the_same_place() -> None:
    events = steps(30)
    stream = Stream()
    for event, stamp in events:
        stream = stream.consume(event, at=stamp)

    assert stream.progress == progress_module.fold(events)
