"""Warm-up-gated, smoothed ETA for the download windows.

The first rate samples of a download are wildly noisy, so an early ETA is
meaningless: 700 s one moment, 100 s the next. Every place that shows time
left (the queue window, the check window, and the per-step ETA in the status
file) feeds its byte counts through :class:`EtaSmoother`: nothing is shown
until a short warm-up elapses, and after that the rate is an exponential
moving average, so one spike cannot swing the estimate.
"""

from __future__ import annotations

import time
from collections.abc import Callable

__all__ = ["ETA_ALPHA", "WARMUP_FRACTION", "WARMUP_SECONDS", "EtaSmoother"]

WARMUP_SECONDS = 3.0
"""Elapsed time that ends the warm-up on its own."""

WARMUP_FRACTION = 0.02
"""Share of the total bytes that ends the warm-up early."""

ETA_ALPHA = 0.3
"""EMA weight of the newest rate sample; the window keeps 1 - alpha."""


class EtaSmoother:
    """An EMA of the observed byte rate behind a small warm-up window.

    Args:
        clock: Seconds source, injectable so tests can fake time. Defaults
            to :func:`time.monotonic`.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        """Start a warm-up window using ``clock`` (defaults to monotonic time)."""
        self._clock = clock
        self._start = self._clock()
        self._rate = 0.0
        self._warmed = False

    @property
    def rate(self) -> float:
        """The smoothed bytes/second, or 0.0 before the first good sample."""
        return self._rate

    def reset(self) -> None:
        """Open a fresh warm-up window and drop the smoothed rate.

        Called when a new job or window starts so an earlier download does
        not leak its rate into the next one.
        """
        self._start = self._clock()
        self._rate = 0.0
        self._warmed = False

    def observe(self, rate: float) -> float:
        """Fold one bytes/second sample into the average and return it.

        Samples at or below zero are ignored, so a stalled download keeps
        its last smoothed rate instead of resetting to zero.
        """
        if rate > 0:
            if self._rate <= 0:
                self._rate = rate
            else:
                self._rate = ETA_ALPHA * rate + (1.0 - ETA_ALPHA) * self._rate
        return self._rate

    def eta(self, done: int, total: int, rate: float) -> float | None:
        """Return the remaining seconds at the smoothed rate.

        Args:
            done: Bytes received so far.
            total: Total bytes expected.
            rate: The newest bytes/second sample.

        Returns:
            ``None`` during warm-up, when nothing has been measured, or
            when nothing remains; otherwise ``(total - done) / rate`` with
            the EMA rate.
        """
        self.observe(rate)
        remaining = total - done
        if self._rate <= 0 or remaining <= 0:
            return None
        if not self._warmed:
            by_time = self._clock() - self._start >= WARMUP_SECONDS
            by_bytes = total > 0 and done >= total * WARMUP_FRACTION
            if not (by_time or by_bytes):
                return None
            self._warmed = True
        return remaining / self._rate
