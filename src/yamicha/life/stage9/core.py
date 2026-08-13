"""Core integration and result intake for the first capability."""

from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from yamicha.contracts import (
    CapabilityResultEvent,
    CapabilityUseProposal,
    ExternalTime,
    IntegratedCapabilityRequest,
)
from yamicha.life.stage8 import Stage8Core


class Stage9Core(Stage8Core):
    def __init__(
        self,
        *,
        capability_request_id_factory: Callable[[], str] | None = None,
        capability_finalization_id_factory: Callable[[], str] | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self._capability_request_id_factory = (
            capability_request_id_factory or (lambda: str(uuid4()))
        )
        self._capability_finalization_id_factory = (
            capability_finalization_id_factory or (lambda: str(uuid4()))
        )
        self._capability_requests: dict[str, IntegratedCapabilityRequest] = {}
        self._capability_result_events: dict[str, CapabilityResultEvent] = {}

    def integrate_capability_request(
        self,
        proposal: CapabilityUseProposal,
        integrated_at: ExternalTime,
    ) -> IntegratedCapabilityRequest:
        request = IntegratedCapabilityRequest(
            request_id=self._capability_request_id_factory(),
            proposal=proposal,
            core_finalization_id=self._capability_finalization_id_factory(),
            integrated_at=integrated_at,
        )
        self._capability_requests[request.request_id] = request
        return request

    def issued_capability_request(
        self,
        request_id: str,
    ) -> IntegratedCapabilityRequest | None:
        return self._capability_requests.get(request_id)

    def receive_capability_result(self, event: CapabilityResultEvent) -> None:
        if event.result.request_id not in self._capability_requests:
            raise ValueError("capability result does not match a Core request")
        if event.event_id in self._capability_result_events:
            raise ValueError("capability result event was already received by Core")
        self._capability_result_events[event.event_id] = event

    @property
    def capability_result_events(self) -> tuple[CapabilityResultEvent, ...]:
        return tuple(self._capability_result_events.values())
