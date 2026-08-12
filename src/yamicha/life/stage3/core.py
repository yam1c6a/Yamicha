"""Core's stage-3 lifecycle registration and request routing."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import timedelta
from uuid import uuid4

from yamicha.contracts import (
    Confidentiality,
    ContentTrust,
    ExternalTime,
    InputCycleStatus,
    InputQuality,
    JudgmentStartRequest,
    OrganRequest,
    OrganResponse,
    ReferenceReader,
    RegisteredJudgmentStart,
    RequestKind,
    RequestStatus,
    ResponsibilityCategory,
    ResponsibilityId,
    RoutedInputCycle,
    SensoryEvent,
)
from yamicha.life.ports import CORE_DEFINITION, ORGAN_DEFINITIONS
from yamicha.life.stage2 import Stage2Core


class Stage3Core(Stage2Core):
    definition = CORE_DEFINITION

    _REFERENCE_DESTINATIONS = (
        ResponsibilityId.STATE,
        ResponsibilityId.MEMORY,
        ResponsibilityId.RELATIONSHIP,
    )

    def __init__(
        self,
        readers: Mapping[ResponsibilityId, ReferenceReader],
        *,
        request_id_factory: Callable[[], str] | None = None,
    ) -> None:
        missing = set(self._REFERENCE_DESTINATIONS) - set(readers)
        if missing:
            raise ValueError(f"stage-3 Core is missing reference readers: {missing}")
        self._readers = dict(readers)
        self._request_id_factory = request_id_factory or (lambda: str(uuid4()))
        self._cycles: dict[str, RoutedInputCycle] = {}
        self._request_transitions: dict[str, tuple[RequestStatus, ...]] = {}
        self._judgment_starts: list[RegisteredJudgmentStart] = []
        self._judgment_start_ids: set[str] = set()

    @property
    def lifecycle_count(self) -> int:
        return len(self._cycles)

    @property
    def judgment_start_count(self) -> int:
        return len(self._judgment_starts)

    def request_transitions(self, request_id: str) -> tuple[RequestStatus, ...]:
        return self._request_transitions[request_id]

    def accept_event(self, event: SensoryEvent) -> RoutedInputCycle:
        if event.correlation_id in self._cycles:
            raise ValueError("lifecycle correlation ID is already registered")
        if event.content_trust is not ContentTrust.UNTRUSTED:
            raise ValueError("external content must remain semantically untrusted")
        if event.quality is InputQuality.DUPLICATE:
            cycle = RoutedInputCycle(
                lifecycle_id=event.correlation_id,
                event=event,
                status=InputCycleStatus.DUPLICATE_RECORDED,
                requests=(),
                responses=(),
            )
            self._cycles[event.correlation_id] = cycle
            return cycle

        requests: list[OrganRequest] = []
        responses: list[OrganResponse] = []
        for destination in self._REFERENCE_DESTINATIONS:
            request = self._make_reference_request(event, destination)
            self._request_transitions[request.request_id] = (
                RequestStatus.RECEIVED,
                RequestStatus.ACCEPTED,
                RequestStatus.RUNNING,
            )
            response = self._readers[destination].read_reference(request)
            if response.request_id != request.request_id:
                raise ValueError("response request ID does not match routed request")
            if response.responder is not destination:
                raise ValueError("response came from an unexpected responsibility")
            self._request_transitions[request.request_id] += (response.status,)
            requests.append(request)
            responses.append(response)
        cycle = RoutedInputCycle(
            lifecycle_id=event.correlation_id,
            event=event,
            status=InputCycleStatus.ROUTED,
            requests=tuple(requests),
            responses=tuple(responses),
        )
        self._cycles[event.correlation_id] = cycle
        return cycle

    def accept_judgment_start(
        self,
        request: JudgmentStartRequest,
    ) -> RegisteredJudgmentStart:
        organ_ids = {
            definition.identifier
            for definition in ORGAN_DEFINITIONS
            if definition.category is ResponsibilityCategory.ORGAN
        }
        if request.source not in organ_ids:
            raise ValueError("judgment start must originate from an organ")
        if (
            not request.request_id.strip()
            or not request.lifecycle_id.strip()
            or not request.purpose.strip()
            or not request.evidence.reference.strip()
        ):
            raise ValueError("judgment start requires purpose and observation evidence")
        if request.request_id in self._judgment_start_ids:
            raise ValueError("judgment start request ID is already registered")
        registered = RegisteredJudgmentStart(
            request=request,
            status=RequestStatus.ACCEPTED,
        )
        self._judgment_starts.append(registered)
        self._judgment_start_ids.add(request.request_id)
        return registered

    def _make_reference_request(
        self,
        event: SensoryEvent,
        destination: ResponsibilityId,
    ) -> OrganRequest:
        request_id = self._request_id_factory()
        if not request_id.strip() or request_id in self._request_transitions:
            raise ValueError("request ID must be non-empty and unique")
        return OrganRequest(
            request_id=request_id,
            lifecycle_id=event.correlation_id,
            kind=RequestKind.REFERENCE,
            source=ResponsibilityId.CORE,
            destination=destination,
            purpose="collect current context for a received sensory event",
            target=destination.value,
            input_references=(event.event_id, event.raw_reference),
            expected_result="read-only context availability",
            authority_context="stage3.read-only-context",
            confidentiality=Confidentiality.PRIVATE,
            created_at=event.received_at,
            deadline=ExternalTime(event.received_at.value + timedelta(seconds=30)),
            causation_id=event.event_id,
        )
