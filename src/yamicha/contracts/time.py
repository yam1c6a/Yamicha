"""Distinct time concepts shared across stage-2 boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True, slots=True)
class ExternalTime:
    """A timezone-aware observation from the body's wall-clock source."""

    value: datetime

    def __post_init__(self) -> None:
        if self.value.tzinfo is None:
            raise ValueError("external time must include timezone information")


@dataclass(frozen=True, slots=True)
class MonotonicTime:
    """A technical monotonic-clock observation owned by the runtime."""

    seconds: float

    def __post_init__(self) -> None:
        if self.seconds < 0:
            raise ValueError("monotonic time must not be negative")


@dataclass(frozen=True, slots=True)
class ElapsedTime:
    """Elapsed duration derived from monotonic observations."""

    value: timedelta

    def __post_init__(self) -> None:
        if self.value < timedelta(0):
            raise ValueError("elapsed time must not be negative")

    @classmethod
    def zero(cls) -> ElapsedTime:
        return cls(timedelta(0))


@dataclass(frozen=True, slots=True)
class InternalTime:
    """Time elapsed inside Yamicha, owned and advanced by State."""

    elapsed_since_start: timedelta
    updated_at: ExternalTime

    def __post_init__(self) -> None:
        if self.elapsed_since_start < timedelta(0):
            raise ValueError("internal time must not be negative")

    @classmethod
    def initial(cls, observed_at: ExternalTime) -> InternalTime:
        return cls(timedelta(0), observed_at)

    def advance(
        self,
        elapsed: ElapsedTime,
        observed_at: ExternalTime,
    ) -> InternalTime:
        return InternalTime(
            elapsed_since_start=self.elapsed_since_start + elapsed.value,
            updated_at=observed_at,
        )


@dataclass(frozen=True, slots=True)
class ClockObservation:
    """Paired external and monotonic readings from one body-side observation."""

    external: ExternalTime
    monotonic: MonotonicTime
