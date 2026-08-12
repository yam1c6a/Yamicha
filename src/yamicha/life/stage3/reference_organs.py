"""Read-only stage-3 recipients for Core-routed context requests."""

from __future__ import annotations

from yamicha.contracts import (
    MessageEnvelope,
    OrganRequest,
    OrganResponse,
    RequestKind,
    RequestStatus,
    ResponsibilityId,
    UnimplementedResponsibilityError,
)
from yamicha.life.ports import (
    MEMORY_DEFINITION,
    RELATIONSHIP_DEFINITION,
    STATE_DEFINITION,
)
from yamicha.life.stage2 import Stage2State


class _Stage3ReferenceReader:
    definition = MEMORY_DEFINITION

    def read_reference(self, request: OrganRequest) -> OrganResponse:
        if request.kind is not RequestKind.REFERENCE:
            return self._rejected(request, "only reference requests are supported")
        if request.destination is not self.definition.identifier:
            return self._rejected(request, "request destination does not match recipient")
        if request.source is not ResponsibilityId.CORE:
            return self._rejected(request, "request did not arrive through Core")
        return OrganResponse(
            request_id=request.request_id,
            responder=self.definition.identifier,
            status=RequestStatus.SUCCEEDED,
            result_reference=(
                f"{self.definition.identifier.value}:stage3:{request.lifecycle_id}"
            ),
            confirmed_effects=(),
            unconfirmed_effects=(),
            error=None,
            occurred_at=request.created_at,
            uncertainty="stage-3 reader exposes only availability, not content",
        )

    def handle(self, message: MessageEnvelope) -> MessageEnvelope:
        raise UnimplementedResponsibilityError(
            "generic organ message handling starts after stage 3"
        )

    def _rejected(self, request: OrganRequest, error: str) -> OrganResponse:
        return OrganResponse(
            request_id=request.request_id,
            responder=self.definition.identifier,
            status=RequestStatus.REJECTED,
            result_reference=None,
            confirmed_effects=(),
            unconfirmed_effects=(),
            error=error,
            occurred_at=request.created_at,
            uncertainty=None,
        )


class Stage3State(Stage2State, _Stage3ReferenceReader):
    definition = STATE_DEFINITION


class Stage3Memory(_Stage3ReferenceReader):
    definition = MEMORY_DEFINITION


class Stage3Relationship(_Stage3ReferenceReader):
    definition = RELATIONSHIP_DEFINITION
