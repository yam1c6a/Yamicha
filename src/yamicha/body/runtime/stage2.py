"""Minimal runtime for startup, periodic opportunities, waiting, and stopping."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from enum import StrEnum
from uuid import uuid4

from yamicha.contracts import (
    ClockObservation,
    ElapsedTime,
    ExecutionOpportunity,
    ExecutionOpportunityKind,
    MessageEnvelope,
    UnimplementedResponsibilityError,
)

from .clock import Clock, SystemClock
from .port import RUNTIME_DEFINITION


class RuntimeStatus(StrEnum):
    """Technical process status, distinct from State's operating state."""

    NOT_STARTED = "not_started"
    ACTIVE = "active"
    WAITING = "waiting"
    STOPPED = "stopped"


class RuntimeStateError(RuntimeError):
    pass


class ClockDiscontinuityError(RuntimeError):
    """Raised instead of silently treating a clock change as normal elapsed time."""


class Stage2Runtime:
    definition = RUNTIME_DEFINITION

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        id_factory: Callable[[], str] | None = None,
        clock_tolerance: timedelta = timedelta(seconds=5),
    ) -> None:
        if clock_tolerance < timedelta(0):
            raise ValueError("clock_tolerance must not be negative")
        self._clock = clock or SystemClock()
        self._id_factory = id_factory or (lambda: str(uuid4()))
        self._clock_tolerance = clock_tolerance
        self._status = RuntimeStatus.NOT_STARTED
        self._last_observation: ClockObservation | None = None
        self._sequence = 0

    @property
    def status(self) -> RuntimeStatus:
        return self._status

    def start(self) -> ExecutionOpportunity:
        if self._status is not RuntimeStatus.NOT_STARTED:
            raise RuntimeStateError(f"cannot start runtime from {self._status}")
        observation = self._clock.observe()
        self._last_observation = observation
        self._status = RuntimeStatus.ACTIVE
        return self._make_opportunity(
            ExecutionOpportunityKind.STARTUP,
            observation,
            ElapsedTime.zero(),
        )

    def periodic_opportunity(self) -> ExecutionOpportunity:
        if self._status is not RuntimeStatus.WAITING:
            raise RuntimeStateError(
                f"periodic opportunity requires waiting runtime: {self._status}"
            )
        previous = self._last_observation
        if previous is None:
            raise RuntimeStateError("runtime has no previous clock observation")
        current = self._clock.observe()
        elapsed_seconds = current.monotonic.seconds - previous.monotonic.seconds
        if elapsed_seconds < 0:
            raise ClockDiscontinuityError("monotonic clock moved backward")
        elapsed = ElapsedTime(timedelta(seconds=elapsed_seconds))
        external_elapsed = current.external.value - previous.external.value
        if abs(external_elapsed - elapsed.value) > self._clock_tolerance:
            raise ClockDiscontinuityError(
                "external clock change does not match monotonic elapsed time"
            )
        self._last_observation = current
        self._status = RuntimeStatus.ACTIVE
        return self._make_opportunity(
            ExecutionOpportunityKind.PERIODIC,
            current,
            elapsed,
        )

    def wait(self) -> None:
        if self._status is not RuntimeStatus.ACTIVE:
            raise RuntimeStateError(f"cannot wait from {self._status}")
        self._status = RuntimeStatus.WAITING

    def stop(self) -> None:
        if self._status not in {RuntimeStatus.ACTIVE, RuntimeStatus.WAITING}:
            raise RuntimeStateError(f"cannot stop runtime from {self._status}")
        self._status = RuntimeStatus.STOPPED

    def handle(self, message: MessageEnvelope) -> MessageEnvelope:
        raise UnimplementedResponsibilityError(
            "generic runtime message delivery starts after stage 2"
        )

    def _make_opportunity(
        self,
        kind: ExecutionOpportunityKind,
        observation: ClockObservation,
        elapsed: ElapsedTime,
    ) -> ExecutionOpportunity:
        opportunity_id = self._id_factory()
        if not opportunity_id.strip():
            raise ValueError("runtime id_factory returned an empty identifier")
        self._sequence += 1
        return ExecutionOpportunity(
            opportunity_id=opportunity_id,
            sequence=self._sequence,
            kind=kind,
            external_time=observation.external,
            monotonic_time=observation.monotonic,
            elapsed_since_previous=elapsed,
        )
