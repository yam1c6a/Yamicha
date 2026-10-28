"""Core authenticates the current experience request and records used versions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from yamicha.contracts import (
    BoundaryDecisionMaterial, DecisionDirection, DialogueOutput, DialogueSpeaker, DialogueTurn,
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
        self._dialogue_input_limit: int | None = None

    def bind_dialogue_input_limit(self, max_characters: int) -> None:
        if self._dialogue_input_limit is not None or max_characters <= 0:
            raise ValueError("dialogue input limit must be positive and bound once")
        self._dialogue_input_limit = max_characters

    def bind_experience_validator(self, validator: Callable[[ExperienceChangeRequest], bool]) -> None:
        if self._experience_request_validator is not None:
            raise RuntimeError("experience validator is already bound")
        self._experience_request_validator = validator

    def build_judgment_context(self, cycle: RoutedInputCycle, boundary: BoundaryDecisionMaterial) -> JudgmentContext:
        context = super().build_judgment_context(cycle, boundary)
        exchange = self._validated_confirmation_exchange(context)
        if not exchange:
            return context
        return replace(context, memory=replace(
            context.memory, experience_confirmation_reference=exchange[-1].source_reference,
        ))

    def experience_confirmation_exchange(self, context: JudgmentContext) -> tuple[DialogueTurn, ...]:
        if context.memory.experience_confirmation_reference is None:
            return ()
        exchange = self._validated_confirmation_exchange(context)
        if not exchange or exchange[-1].source_reference != context.memory.experience_confirmation_reference:
            return ()
        return exchange

    def _validated_confirmation_exchange(self, context: JudgmentContext) -> tuple[DialogueTurn, ...]:
        """Allow one fixed clarification exchange, without restoring its materials.

        Current-query mismatch may hide an otherwise valid exchange. Expired or
        changed sources, incomplete legacy traces and the input limit still win.
        """
        window = context.memory.experience_information
        relationship = self._relationship_material_provider
        if window is None or not isinstance(relationship, Stage15Relationship):
            return ()
        active = relationship.active_dialogue_context
        if (active is None or active.counterpart_id != context.event.source_id
                or active.context_id != window.context_id or len(active.turns) < 2):
            return ()
        # Only the immediately preceding exchange can supply a clarification.
        # A management input, silence, or another reply ends this one-use path.
        previous = active.turns[-1]
        question = active.turns[-2]
        if (previous.speaker is not DialogueSpeaker.YAMICHA
                or question.speaker is not DialogueSpeaker.HUMAN
                or question.lifecycle_id != previous.lifecycle_id):
            return ()
        expected = {
            self._confirmation_reference(selection.item.item_id, selection.item.version)
            for selection in window.selections
            if selection.item.value is ExperienceValue.ASK_FIRST
        }
        if not expected:
            return ()
        record = next((record for record in reversed(self._records)
                       if record.lifecycle_id == previous.lifecycle_id), None)
        if record is None or not any(
            entry.kind is RecordKind.RESULT and entry.source_reference == previous.source_reference
            for entry in record.entries
        ):
            return ()
        if not any(entry.kind is RecordKind.EXPRESSION
                   and entry.source_owner is ResponsibilityId.CORE
                   and entry.source_reference in expected for entry in record.entries):
            return ()
        memory = self._stage6_memory
        assert isinstance(memory, Stage16Memory)
        relationship_window = context.relationship.relationship_information
        if relationship_window is None:
            return ()
        memory_window = memory.selected_window(context.lifecycle_id)
        excluded = self.conversation_memory_dialogue_exclusions(memory_window, exclude_unselected=False)
        excluded.update(record.lifecycle_id for record in memory.conversation_memory_governance_records)
        excluded.update(relationship.dialogue_exclusions(
            context.event, relationship_window, exclude_unselected=False,
        ))
        active_experiences = {(item.item_id, item.version) for item in memory.experience_items if item.active}
        excluded.update(usage.lifecycle_id for usage in memory.experience_usages
                        if not set(usage.item_versions).issubset(active_experiences))
        if previous.lifecycle_id in self.dependent_dialogue_lifecycles(excluded):
            return ()
        exchange = (question, previous)
        characters = (len(context.event.meaning.normalized_text)
                      + sum(len(turn.text) for turn in exchange)
                      + window.total_characters + relationship_window.total_characters
                      + (0 if memory_window is None else memory_window.total_characters))
        if self._dialogue_input_limit is None or characters > self._dialogue_input_limit:
            return ()
        return exchange

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
