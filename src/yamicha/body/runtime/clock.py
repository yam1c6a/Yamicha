"""Body-side clock observations for the stage-2 runtime."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Protocol

from yamicha.contracts import ClockObservation, ExternalTime, MonotonicTime


class Clock(Protocol):
    def observe(self) -> ClockObservation:
        """Return paired external and monotonic readings."""
        ...


class SystemClock:
    def observe(self) -> ClockObservation:
        return ClockObservation(
            external=ExternalTime(datetime.now(UTC)),
            monotonic=MonotonicTime(time.monotonic()),
        )
