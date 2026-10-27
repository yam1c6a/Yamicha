"""Compose confirmed relationship continuity without granting external authority."""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import ClassVar
from uuid import uuid4

from yamicha.contracts import (
    AuxiliaryIntelligenceProposal,
    AuxiliaryIntelligenceResult,
    DecisionDirection,
    DialogueOutput,
    ExternalTime,
    JudgmentContext,
    JudgmentFinalization,
    LifecycleRecord,
    RawTextInput,
    RelationshipInformationItem,
    SensoryEvent,
    Stage14InputOutcome,
    Stage15InputOutcome,
)
from yamicha.life.stage15 import Stage15Core, Stage15Judgment, Stage15Relationship

from .stage14 import Stage14System, Stage14TextInputRunner, make_stage14_system


CONFIGURATION_VERSION = "stage15-v1"


class Stage15TextInputRunner(Stage14TextInputRunner):
    def __init__(
        self,
        *,
        core: Stage15Core,
        judgment: Stage15Judgment,
        relationship: Stage15Relationship,
        **kwargs: object,
    ) -> None:
        super().__init__(core=core, judgment=judgment, relationship=relationship, **kwargs)
        self._stage15_core = core
        self._stage15_judgment = judgment
        self._stage15_relationship = relationship
        self._relationship_window = None
        self._relationship_request = None
        self._relationship_record = None
        self._relationship_pending = None

    def run(self, raw: RawTextInput) -> Stage15InputOutcome:
        self._relationship_window = None
        self._relationship_request = None
        self._relationship_record = None
        self._relationship_pending = None
        base = super().run(raw)
        return Stage15InputOutcome(
            **{field.name: getattr(base, field.name) for field in fields(Stage14InputOutcome)},
            relationship_information=self._relationship_window,
            relationship_change_request=self._relationship_request,
            relationship_change_record=self._relationship_record,
            relationship_pending_confirmation=self._relationship_pending,
        )

    def _allows_dialogue_assistance(self, event: SensoryEvent) -> bool:
        return not self._stage15_judgment.is_relationship_command(event.meaning.normalized_text)

    def _prepare_memory_context(self, event: SensoryEvent) -> None:
        remaining = max(
            0, self._intelligence_constraints.max_input_characters
            - len(event.meaning.normalized_text),
        )
        self._relationship_window = self._stage15_relationship.select_relationship_information(
            event, max_items=5, max_characters=min(512, remaining),
        )
        remaining -= self._relationship_window.total_characters
        self._current_memory_window = self._stage13_memory.prepare_selection(
            event,
            max_items=self._conversation_memory_max_items,
            max_characters=min(self._conversation_memory_max_characters, remaining),
        )

    def _propose_dialogue_assistance(
        self,
        context: JudgmentContext,
        event: SensoryEvent,
        proposed_at: ExternalTime,
    ) -> AuxiliaryIntelligenceProposal:
        assert self._relationship_window is not None
        memory_characters = (
            0 if self._current_memory_window is None
            else self._current_memory_window.total_characters
        )
        dialogue = self._stage15_relationship.select_dialogue_window(
            event,
            max_exchanges=self._dialogue_max_exchanges,
            max_characters=(
                self._intelligence_constraints.max_input_characters
                - memory_characters - self._relationship_window.total_characters
            ),
            relationship_information=self._relationship_window,
        )
        self._last_dialogue_window = dialogue
        return self._stage15_judgment.propose_dialogue_assistance(
            lifecycle_id=context.lifecycle_id,
            model=self._intelligence_model,
            input_text=event.meaning.normalized_text,
            input_source_reference=event.event_id,
            constraints=self._intelligence_constraints,
            proposed_at=proposed_at,
            dialogue_context=dialogue,
            conversation_memory=self._current_memory_window,
            relationship_information=self._relationship_window,
        )

    def _record_long_term_memory(
        self,
        event: SensoryEvent,
        dialogue_output: DialogueOutput,
        record: LifecycleRecord,
        finalization: JudgmentFinalization,
        intelligence_result: AuxiliaryIntelligenceResult | None,
    ) -> None:
        owner = self._stage15_relationship
        if (
            self._stage15_judgment.is_relationship_command(event.meaning.normalized_text)
            and finalization.finalized_direction is not DecisionDirection.RESPOND
        ):
            return
        request = self._stage15_judgment.propose_relationship_change(
            event,
            owner.relationship_items,
            owner.pending_relationship_confirmation_for(event.source_id),
        )
        if request is not None:
            self._relationship_request = request
            self._relationship_record = self._stage15_core.route_relationship_change(
                request, event, finalization,
            )
            self._relationship_pending = owner.pending_relationship_confirmation_for(event.source_id)
            return
        # Record the complete relationship material consulted by Judgment, even
        # if the LLM receives a smaller window or is unavailable.
        window = owner.select_relationship_information(event)
        owner.record_relationship_usage(event, window)
        super()._record_long_term_memory(
            event, dialogue_output, record, finalization, intelligence_result,
        )


@dataclass(slots=True, kw_only=True)
class Stage15System(Stage14System):
    stage_label: ClassVar[int] = 15
    core: Stage15Core
    judgment: Stage15Judgment
    relationship: Stage15Relationship
    text_input_runner: Stage15TextInputRunner

    def list_relationship_information(
        self, counterpart_id: str, *, include_inactive: bool = False,
    ) -> tuple[RelationshipInformationItem, ...]:
        return self.relationship.list_relationship_information(
            counterpart_id, include_inactive=include_inactive,
        )


def make_stage15_system(**stage14_options: object) -> Stage15System:
    base = make_stage14_system(
        _configuration_version=CONFIGURATION_VERSION,
        _upgrade_from_configuration_versions=("stage13-v1", "stage14-v1"),
        _core_factory=Stage15Core,
        _judgment_factory=Stage15Judgment,
        _relationship_factory=Stage15Relationship,
        _intelligence_allowed_input_scope=(
            "current_verified_text",
            "verified_recent_dialogue_turns",
            "selected_adopted_conversation_memories",
            "selected_confirmed_relationship_information",
            "verified_speaker_and_model_identity",
        ),
        **stage14_options,
    )
    base.core.bind_relationship_validator(base.judgment.issued_relationship_request)
    runner = Stage15TextInputRunner(
        protection_boundary=base.protection_boundary,
        sensation=base.sensation,
        core=base.core,
        judgment=base.judgment,
        language=base.language,
        auxiliary_intelligence=base.auxiliary_intelligence,
        intelligence_model=base.intelligence_model,
        intelligence_constraints=base.intelligence_constraints,
        correlation_id_factory=stage14_options.get("input_correlation_id_factory") or (lambda: str(uuid4())),
        trace_id_factory=stage14_options.get("intelligence_trace_id_factory"),
        persistence_store=base.persistence,
        snapshot_coordinator=base.snapshot_coordinator,
        relationship=base.relationship,
        dialogue_max_exchanges=base.dialogue_max_exchanges,
        memory=base.memory,
        conversation_memory_max_items=base.conversation_memory_max_items,
        conversation_memory_max_characters=base.conversation_memory_max_characters,
        checkpoint_generations=base.retention_policy.checkpoint_generations,
    )
    values = {field.name: getattr(base, field.name) for field in fields(Stage14System)}
    values["text_input_runner"] = runner
    return Stage15System(**values)
