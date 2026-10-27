"""Core authenticates the current experience request and records used versions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from yamicha.contracts import (
    BoundaryDecisionMaterial, DecisionDirection, DialogueOutput, DialogueSpeaker,
    ExpressionArtifact, ExpressionReview,
    ExpressionItem, ExpressionMeaning, StatementKind,
    FinalizationStatus, InputQuality, JudgmentContext, JudgmentFinalization,
    JudgmentResult, LifecycleRecord, OutputReleaseStatus, RecordKind, ResponsibilityId,
    RoutedInputCycle, SensoryEvent,
)
from yamicha.contracts.experience import (
    ExperienceChangeRecord, ExperienceChangeRequest, ExperienceValue,
)
from yamicha.life.stage15 import Stage15Core, Stage15Relationship

from .memory import Stage16Memory
from .judgment import EXPERIENCE_DIALOGUE_CONFIRMATION


class Stage16Core(Stage15Core):
    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._experience_request_validator: Callable[[ExperienceChangeRequest], bool] | None = None

    def bind_experience_validator(self, validator: Callable[[ExperienceChangeRequest], bool]) -> None:
        if self._experience_request_validator is not None:
            raise RuntimeError("experience validator is already bound")
        self._experience_request_validator = validator

    def build_judgment_context(self, cycle: RoutedInputCycle, boundary: BoundaryDecisionMaterial) -> JudgmentContext:
        context = super().build_judgment_context(cycle, boundary)
        window = context.memory.experience_information
        relationship = self._relationship_material_provider
        if window is None or not isinstance(relationship, Stage15Relationship):
            return context
        active = relationship.active_dialogue_context
        if (active is None or active.counterpart_id != context.event.source_id
                or active.context_id != window.context_id or not active.turns):
            return context
        # Only the immediately preceding exchange can supply a clarification.
        # A management input, silence, or another reply ends this one-use path.
        previous = active.turns[-1]
        if previous.speaker is not DialogueSpeaker.YAMICHA:
            return context
        expected = {
            self._confirmation_reference(selection.item.item_id, selection.item.version)
            for selection in window.selections
            if selection.item.value is ExperienceValue.ASK_FIRST
        }
        if not expected:
            return context
        record = next((record for record in reversed(self._records)
                       if record.lifecycle_id == previous.lifecycle_id), None)
        if record is None or not any(
            entry.kind is RecordKind.RESULT and entry.source_reference == previous.source_reference
            for entry in record.entries
        ):
            return context
        if not any(entry.kind is RecordKind.EXPRESSION
                   and entry.source_owner is ResponsibilityId.CORE
                   and entry.source_reference in expected for entry in record.entries):
            return context
        return replace(context, memory=replace(
            context.memory, experience_confirmation_reference=previous.source_reference,
        ))

    @staticmethod
    def _confirmation_reference(item_id: str, version: int) -> str:
        return f"experience-confirmation:{item_id}:v{version}"

    def route_experience_change(self, request: ExperienceChangeRequest, event: SensoryEvent, finalization: JudgmentFinalization) -> ExperienceChangeRecord:
        if self._experience_request_validator is None or not self._experience_request_validator(request):
            raise ValueError("experience proposal was not issued by Judgment")
        cycle = self._cycles.get(event.correlation_id)
        if (cycle is None or cycle.event != event or event.quality is not InputQuality.VALID
            or request.lifecycle_id != event.correlation_id or request.counterpart_id != event.source_id
            or request.source_reference != event.event_id or request.requested_at != event.received_at
            or self._relationship_finalizations.get(event.correlation_id) != finalization
            or finalization.status is not FinalizationStatus.FINALIZED
            or finalization.finalized_direction is not DecisionDirection.RESPOND):
            raise ValueError("experience change requires the current finalized response lifecycle")
        relationship = self._relationship_material_provider
        if not isinstance(relationship, Stage15Relationship) or not isinstance(self._stage6_memory, Stage16Memory):
            raise TypeError("stage-16 owners were not composed")
        active = relationship.active_dialogue_context
        if active is None or active.counterpart_id != event.source_id or active.context_id != request.context_id:
            raise ValueError("experience change must belong to the known counterpart's current dialogue")
        return self._stage6_memory.apply_experience_change(request)

    def record_completed_lifecycle(self, *, context: JudgmentContext, judgment: JudgmentResult,
        finalization: JudgmentFinalization, expression: ExpressionArtifact,
        expression_review: ExpressionReview, dialogue_output: DialogueOutput,
    ) -> LifecycleRecord:
        record = super().record_completed_lifecycle(context=context, judgment=judgment,
            finalization=finalization, expression=expression, expression_review=expression_review,
            dialogue_output=dialogue_output)
        window = context.memory.experience_information
        if window is None or not window.selections:
            return record
        entries = tuple(self._entry(lifecycle_id=context.lifecycle_id, kind=RecordKind.JUDGMENT,
            owner=ResponsibilityId.JUDGMENT, reference=f"experience:{s.item.item_id}:v{s.item.version}",
            summary=f"confirmed experience applied within dialogue scope: {s.item.target.value}={s.item.value.value}",
            occurred_at=context.event.received_at) for s in window.selections)
        if (dialogue_output.status is OutputReleaseStatus.RELEASED
                and finalization.finalized_direction is DecisionDirection.CONFIRM
                and any(statement.item.meaning is ExpressionMeaning.CONFIRM_DIALOGUE_INTENT
                        for statement in expression.statements)):
            entries += tuple(
                self._entry(
                    lifecycle_id=context.lifecycle_id, kind=RecordKind.EXPRESSION,
                    owner=ResponsibilityId.CORE,
                    reference=self._confirmation_reference(selection.item.item_id, selection.item.version),
                    summary="experience clarification was released for the next input in this dialogue",
                    occurred_at=context.event.received_at,
                )
                for selection in window.selections
                if selection.item.value is ExperienceValue.ASK_FIRST
            )
        updated = replace(record, entries=(*record.entries, *entries))
        self._records[-1] = updated
        return updated

    @staticmethod
    def _expression_items(direction: DecisionDirection, judgment: JudgmentResult, context: JudgmentContext) -> tuple[ExpressionItem, ...]:
        if direction is DecisionDirection.CONFIRM and any(
            candidate.selected and EXPERIENCE_DIALOGUE_CONFIRMATION in candidate.confirmation_conditions
            for candidate in judgment.candidates
        ):
            return (ExpressionItem(kind=StatementKind.CONFIRMATION_REQUEST,
                meaning=ExpressionMeaning.CONFIRM_DIALOGUE_INTENT,
                source_reference=f"judgment:{judgment.lifecycle_id}:{direction.value}"),)
        return Stage15Core._expression_items(direction, judgment, context)
