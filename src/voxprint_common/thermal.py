"""Suite thermal rule.

Full speed for the first 2.5 hours. After that, pause between batches while
the GPU's 5-minute median is at least 83 C, or while it has been throttling
for more than 60 seconds. Resume at or below 75 C.

A missing temperature does not by itself pause the run. A long throttle
reading still does, once the full-speed window is over. The sampler is the
app's: this module only decides.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import assert_never

__all__ = [
    "FULL_SPEED_S",
    "PAUSE_C",
    "RESUME_C",
    "THROTTLE_S",
    "Action",
    "Decision",
    "ThermalTimeout",
    "decide",
    "wait_until_ready",
]

FULL_SPEED_S = 2.5 * 60 * 60
PAUSE_C = 83.0
RESUME_C = 75.0
THROTTLE_S = 60.0


class Action(StrEnum):
    """What to do before the next batch."""

    RUN = "run"
    PAUSE = "pause"
    RESUME = "resume"


class ThermalTimeout(TimeoutError):
    """The GPU stayed hot longer than the caller was willing to wait."""


@dataclass(frozen=True)
class Decision:
    """A thermal action and the reason a log line can show."""

    action: Action
    reason: str


def decide(
    elapsed_s: float,
    median_c: float | None,
    throttling_s: float,
    paused: bool,
) -> Decision:
    """Decide what to do before the next batch.

    Args:
        elapsed_s: Seconds since the job started.
        median_c: Five-minute median temperature in Celsius, or None when
            the sensor could not be read.
        throttling_s: Seconds the GPU has been throttling.
        paused: Whether the job is already paused.

    Returns:
        ``run``, ``pause``, or ``resume``, with a short reason.
    """
    elapsed = max(0.0, float(elapsed_s))
    throttle = max(0.0, float(throttling_s))
    if elapsed < FULL_SPEED_S:
        return Decision(Action.RUN, "full speed for the first 2.5 hours")
    hot = median_c is not None and median_c >= PAUSE_C
    throttling = throttle > THROTTLE_S
    if paused:
        if median_c is not None and median_c <= RESUME_C and not throttling:
            return Decision(Action.RESUME, "GPU at or below 75 C")
        if hot:
            return Decision(Action.PAUSE, "5-minute median is at least 83 C")
        if throttling:
            return Decision(Action.PAUSE, "GPU has been throttling for more than 60 seconds")
        return Decision(Action.PAUSE, "waiting until the GPU is at or below 75 C")
    if hot:
        return Decision(Action.PAUSE, "5-minute median is at least 83 C")
    if throttling:
        return Decision(Action.PAUSE, "GPU has been throttling for more than 60 seconds")
    return Decision(Action.RUN, "within limits")


def wait_until_ready(
    sample: Callable[[], tuple[float | None, float]],
    *,
    started: float,
    paused: bool = False,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
    interval: float = 5.0,
    max_wait: float | None = None,
) -> Decision:
    """Poll ``sample`` until the rule says run or resume.

    Args:
        sample: Returns ``(median_c, throttling_s)``.
        started: Monotonic time when the job started, in the same clock as ``now``.
        paused: Whether the job is already paused on entry.
        sleep: Wait between samples.
        now: Monotonic clock.
        interval: Seconds between samples while paused.
        max_wait: Seconds of pause to tolerate. None waits without a cap.

    Returns:
        The decision that allowed the job to continue. ``paused`` on that
        decision is the state after this call: a ``resume`` means the caller
        should clear its paused flag.

    Raises:
        ThermalTimeout: ``max_wait`` elapsed while the rule still said pause.
    """
    holding = paused
    waited = 0.0
    while True:
        median_c, throttling_s = sample()
        decision = decide(now() - started, median_c, throttling_s, holding)
        if decision.action is Action.RUN or decision.action is Action.RESUME:
            return decision
        if decision.action is not Action.PAUSE:
            assert_never(decision.action)
        holding = True
        if max_wait is not None and waited >= max_wait:
            raise ThermalTimeout(decision.reason)
        step = interval if max_wait is None else min(interval, max(0.0, max_wait - waited))
        sleep(step)
        waited += step
