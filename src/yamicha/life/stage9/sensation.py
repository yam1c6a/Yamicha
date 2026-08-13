"""Sensation reception for direct capability execution results."""

from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from yamicha.contracts import CapabilityResult, CapabilityResultEvent, ExternalTime
from yamicha.life.stage3 import Stage3Sensation


class Stage9Sensation(Stage3Sensation):
    def __init__(
        self,
        *,
        capability_event_id_factory: Callable[[], str] | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self._capability_event_id_factory = (
            capability_event_id_factory or (lambda: str(uuid4()))
        )
        self._seen_capability_result_ids: set[str] = set()
        self._capability_result_events: list[CapabilityResultEvent] = []

    def receive_capability_result(
        self,
        result: CapabilityResult,
        received_at: ExternalTime,
    ) -> CapabilityResultEvent:
        if result.result_id in self._seen_capability_result_ids:
            raise ValueError("duplicate capability result must not be re-received")
        self._seen_capability_result_ids.add(result.result_id)
        event = CapabilityResultEvent(
            event_id=self._required_id(
                self._capability_event_id_factory,
                "capability event",
            ),
            result=result,
            received_at=received_at,
        )
        self._capability_result_events.append(event)
        return event

    @property
    def capability_result_events(self) -> tuple[CapabilityResultEvent, ...]:
        return tuple(self._capability_result_events)
