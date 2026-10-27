"""Core routes authenticated relationship proposals after its own finalization."""

from __future__ import annotations

from collections.abc import Callable

from yamicha.contracts import (
    DecisionDirection, FinalizationStatus, InputQuality,
    JudgmentContext, JudgmentFinalization, JudgmentResult, SensoryEvent,
)
from yamicha.contracts.relationship import RelationshipChangeRecord, RelationshipChangeRequest
from yamicha.life.stage14 import Stage14Core

from .relationship import Stage15Relationship


class Stage15Core(Stage14Core):
    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._relationship_request_validator: Callable[[RelationshipChangeRequest], bool] | None = None
        self._relationship_finalizations: dict[str, JudgmentFinalization] = {}

    def bind_relationship_validator(self, validator: Callable[[RelationshipChangeRequest], bool]) -> None:
        if self._relationship_request_validator is not None:
            raise RuntimeError("relationship validator is already bound")
        self._relationship_request_validator = validator

    def finalize_judgment(self, result: JudgmentResult, context: JudgmentContext) -> JudgmentFinalization:
        finalization = super().finalize_judgment(result, context)
        self._relationship_finalizations[result.lifecycle_id] = finalization
        return finalization

    def route_relationship_change(
        self,
        request: RelationshipChangeRequest,
        event: SensoryEvent,
        finalization: JudgmentFinalization,
    ) -> RelationshipChangeRecord:
        if self._relationship_request_validator is None or not self._relationship_request_validator(request):
            raise ValueError("relationship proposal was not issued by Judgment")
        cycle = self._cycles.get(event.correlation_id)
        if (
            cycle is None or cycle.event != event or event.quality is not InputQuality.VALID
            or request.lifecycle_id != event.correlation_id
            or request.counterpart_id != event.source_id
            or request.source_reference != event.event_id
            or request.requested_at != event.received_at
            or self._relationship_finalizations.get(event.correlation_id) != finalization
            or finalization.status is not FinalizationStatus.FINALIZED
            or finalization.finalized_direction is not DecisionDirection.RESPOND
        ):
            raise ValueError("relationship change requires the current finalized response lifecycle")
        relationship = self._relationship_material_provider
        if not isinstance(relationship, Stage15Relationship):
            raise TypeError("stage-15 Relationship was not composed")
        return relationship.apply_relationship_change(request)
