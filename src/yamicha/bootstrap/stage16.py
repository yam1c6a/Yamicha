"""Compose explicit, confirmed and scoped experience in the dialogue lifecycle."""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from typing import ClassVar
from uuid import uuid4

from yamicha.body.persistence import PersistenceError
from yamicha.body.runtime import RuntimeStatus
from yamicha.contracts import (
    AuxiliaryIntelligenceProposal, AuxiliaryIntelligenceResult, DecisionDirection,
    DialogueOutput, DialogueSpeaker, ExternalTime, JudgmentContext,
    JudgmentFinalization, LifecycleRecord, OutputReleaseStatus, ProtectionMode,
    RawTextInput, SensoryEvent, Stage15InputOutcome, Stage16InputOutcome,
    Stage9CapabilityOutcome,
)
from yamicha.contracts.experience import ExperienceEvidence, ExperienceEvidenceKind, ExperienceItem, ExperienceValue
from yamicha.life.stage16 import Stage16Core, Stage16Judgment, Stage16Memory

from .stage15 import Stage15System, Stage15TextInputRunner, make_stage15_system


CONFIGURATION_VERSION = "stage16-v1"


class Stage16TextInputRunner(Stage15TextInputRunner):
    def __init__(self, *, core: Stage16Core, judgment: Stage16Judgment,
                 memory: Stage16Memory, **kwargs: object) -> None:
        super().__init__(core=core, judgment=judgment, memory=memory, **kwargs)
        self._stage16_core = core
        self._stage16_judgment = judgment
        self._stage16_memory = memory
        self._experience_window = None
        self._experience_request = None
        self._experience_record = None
        self._experience_pending = None

    def run(self, raw: RawTextInput) -> Stage16InputOutcome:
        self._experience_window = None
        self._experience_request = None
        self._experience_record = None
        self._experience_pending = None
        base = super().run(raw)
        return Stage16InputOutcome(
            **{field.name: getattr(base, field.name) for field in fields(Stage15InputOutcome)},
            experience_information=self._experience_window,
            experience_change_request=self._experience_request,
            experience_change_record=self._experience_record,
            experience_pending_confirmation=self._experience_pending,
        )

    def _allows_dialogue_assistance(self, event: SensoryEvent) -> bool:
        return not self._is_management_event(event)

    def _is_management_event(self, event: SensoryEvent) -> bool:
        text = event.meaning.normalized_text
        return self._stage16_judgment.is_management_command(text) or (
            self._stage16_memory.pending_confirmation_for(event.source_id) is not None
            and (self._stage16_judgment.is_confirmation(text) or self._stage16_judgment.is_cancellation(text))
        )

    def _prepare_memory_context(self, event: SensoryEvent) -> None:
        context = self._stage15_relationship.ensure_active_context(event)
        remaining = max(0, self._intelligence_constraints.max_input_characters - len(event.meaning.normalized_text))
        self._experience_window = self._stage16_memory.select_experience_information(
            event, context.context_id, max_items=0 if self._is_management_event(event) else 2,
            max_characters=min(256, remaining),
        )
        remaining -= self._experience_window.total_characters
        self._relationship_window = self._stage15_relationship.select_relationship_information(
            event, max_items=5, max_characters=min(512, remaining),
        )
        remaining -= self._relationship_window.total_characters
        self._current_memory_window = self._stage13_memory.prepare_selection(
            event, max_items=self._conversation_memory_max_items,
            max_characters=min(self._conversation_memory_max_characters, remaining),
        )

    def _propose_dialogue_assistance(self, context: JudgmentContext, event: SensoryEvent,
                                    proposed_at: ExternalTime) -> AuxiliaryIntelligenceProposal:
        assert self._experience_window is not None and self._relationship_window is not None
        window = self._experience_window
        current_versions = {(s.item.item_id, s.item.version) for s in window.selections}
        excluded = {
            usage.lifecycle_id for usage in self._stage16_memory.experience_usages
            if not set(usage.item_versions).issubset(current_versions)
        }
        excluded.update(record.lifecycle_id for record in self._stage16_memory.conversation_memory_governance_records)
        active = self._stage15_relationship.active_dialogue_context
        assert active is not None
        excluded.update(
            turn.lifecycle_id for turn in active.turns
            if turn.speaker is DialogueSpeaker.HUMAN
            and self._stage16_judgment.is_management_command(turn.text)
        )
        memory_characters = 0 if self._current_memory_window is None else self._current_memory_window.total_characters
        dialogue = self._stage15_relationship.select_dialogue_window(
            event, max_exchanges=self._dialogue_max_exchanges,
            max_characters=(self._intelligence_constraints.max_input_characters
                            - memory_characters - self._relationship_window.total_characters
                            - window.total_characters),
            relationship_information=self._relationship_window,
            excluded_lifecycle_ids=tuple(excluded),
        )
        self._last_dialogue_window = dialogue
        constraints = self._intelligence_constraints
        if any(s.item.value is ExperienceValue.BRIEF for s in window.selections):
            constraints = replace(constraints, max_output_characters=min(160, constraints.max_output_characters))
        return self._stage16_judgment.propose_dialogue_assistance(
            lifecycle_id=context.lifecycle_id, model=self._intelligence_model,
            input_text=event.meaning.normalized_text, input_source_reference=event.event_id,
            constraints=constraints, proposed_at=proposed_at, dialogue_context=dialogue,
            conversation_memory=self._current_memory_window,
            relationship_information=self._relationship_window,
            experience_information=window,
            experience_confirmation_reference=context.memory.experience_confirmation_reference,
        )

    def _record_long_term_memory(self, event: SensoryEvent, dialogue_output: DialogueOutput,
                                 record: LifecycleRecord, finalization: JudgmentFinalization,
                                 intelligence_result: AuxiliaryIntelligenceResult | None) -> None:
        memory = self._stage16_memory
        active = self._stage15_relationship.active_dialogue_context
        assert active is not None
        if self._stage16_judgment.is_experience_command(event.meaning.normalized_text):
            if finalization.finalized_direction is not DecisionDirection.RESPOND:
                return
            request = self._stage16_judgment.propose_experience_change(
                event, active.context_id, self._current_experience_evidence(),
                memory.experience_items, memory.pending_experience_confirmation_for(event.source_id),
            )
            if request is not None:
                self._experience_request = request
                self._experience_record = self._stage16_core.route_experience_change(request, event, finalization)
                self._experience_pending = memory.pending_experience_confirmation_for(event.source_id)
            return
        super()._record_long_term_memory(event, dialogue_output, record, finalization, intelligence_result)
        if self._governance_record is not None:
            return
        if self._experience_window is not None:
            memory.record_experience_usage(event, self._experience_window)
        if (not self._stage16_judgment.is_management_command(event.meaning.normalized_text)
                and finalization.finalized_direction in (DecisionDirection.RESPOND, DecisionDirection.CONFIRM)
                and dialogue_output.status is OutputReleaseStatus.RELEASED):
            memory.record_dialogue_evidence(event, dialogue_output, active.context_id)

    def _current_experience_evidence(self) -> tuple[ExperienceEvidence, ...]:
        """Bind dialogue feedback to the last published non-management reply.

        A refusal or held reply can be published without being eligible for
        adaptation. In that case, do not silently fall back to an older reply.
        Capability feedback still names its independently registered result.
        """
        memory = self._stage16_memory
        relationship = self._stage15_relationship
        context = relationship.active_dialogue_context
        assert context is not None
        management_lifecycles = {
            record.lifecycle_id for record in (
                *memory.conversation_memory_governance_records,
                *memory.experience_changes,
                *relationship.relationship_changes,
            )
        }
        management_lifecycles.update(
            turn.lifecycle_id for turn in context.turns
            if turn.speaker is DialogueSpeaker.HUMAN
            and self._stage16_judgment.is_management_command(turn.text)
        )
        latest_output_reference = next((
            turn.source_reference for turn in reversed(context.turns)
            if turn.speaker is DialogueSpeaker.YAMICHA
            and turn.lifecycle_id not in management_lifecycles
        ), None)
        return tuple(
            evidence for evidence in memory.experience_evidence
            if evidence.kind is ExperienceEvidenceKind.CAPABILITY_RESULT
            or (evidence.context_id == context.context_id
                and evidence.source_reference == latest_output_reference)
        )


@dataclass(slots=True, kw_only=True)
class Stage16System(Stage15System):
    stage_label: ClassVar[int] = 16
    core: Stage16Core
    judgment: Stage16Judgment
    memory: Stage16Memory
    text_input_runner: Stage16TextInputRunner

    def list_experiences(self, counterpart_id: str, *, include_inactive: bool = False) -> tuple[ExperienceItem, ...]:
        return self.memory.list_experiences(counterpart_id, include_inactive=include_inactive)

    def use_read_capability(self, *, target: str, authority_id: str, idempotency_key: str,
                            requested_at: ExternalTime,
                            reason: str = "an external text resource is required as judgment material") -> Stage9CapabilityOutcome:
        if self._closed:
            raise RuntimeError("stage-16 system is closed")
        if not self.text_input_runner.persistence_healthy:
            raise RuntimeError("persistence is unavailable after a failed checkpoint")
        if self.protection_boundary.mode is ProtectionMode.PROTECTED:
            raise RuntimeError("capability use is blocked while protection is active")
        if self.runtime.status is RuntimeStatus.NOT_STARTED or self._startup_opportunity is not None:
            self.run_time_cycle()
        if self.runtime.status is not RuntimeStatus.WAITING:
            raise RuntimeError("capability use requires waiting runtime")
        try:
            outcome = Stage15System.use_read_capability(
                self, target=target, authority_id=authority_id, idempotency_key=idempotency_key,
                requested_at=requested_at, reason=reason,
            )
        except PersistenceError:
            self.text_input_runner.mark_persistence_unhealthy()
            raise
        context = self.relationship.active_dialogue_context
        if outcome.event is not None and context is not None and authority_id == context.counterpart_id:
            try:
                if (outcome.event not in self.core.capability_result_events
                        or self.core.issued_capability_request(outcome.request.request_id) != outcome.request):
                    raise ValueError("capability evidence requires the registered execution result")
                self.memory.record_capability_evidence(
                    outcome.event, counterpart_id=authority_id, context_id=context.context_id,
                )
                self.snapshot_coordinator.commit(requested_at)
                self._prune_checkpoint_generations()
            except Exception:
                self.text_input_runner.mark_persistence_unhealthy()
                raise
        return outcome


def make_stage16_system(**stage15_options: object) -> Stage16System:
    base = make_stage15_system(
        _configuration_version=CONFIGURATION_VERSION,
        _upgrade_from_configuration_versions=("stage13-v1", "stage14-v1", "stage15-v1"),
        _core_factory=Stage16Core, _judgment_factory=Stage16Judgment,
        _memory_factory=Stage16Memory,
        _intelligence_allowed_input_scope=(
            "current_verified_text", "verified_recent_dialogue_turns",
            "selected_adopted_conversation_memories", "selected_confirmed_relationship_information",
            "selected_confirmed_experience", "verified_speaker_and_model_identity",
        ),
        **stage15_options,
    )
    base.core.bind_experience_validator(base.judgment.issued_experience_request)
    runner = Stage16TextInputRunner(
        protection_boundary=base.protection_boundary, sensation=base.sensation,
        core=base.core, judgment=base.judgment, language=base.language,
        auxiliary_intelligence=base.auxiliary_intelligence,
        intelligence_model=base.intelligence_model, intelligence_constraints=base.intelligence_constraints,
        correlation_id_factory=stage15_options.get("input_correlation_id_factory") or (lambda: str(uuid4())),
        trace_id_factory=stage15_options.get("intelligence_trace_id_factory"),
        persistence_store=base.persistence, snapshot_coordinator=base.snapshot_coordinator,
        relationship=base.relationship, dialogue_max_exchanges=base.dialogue_max_exchanges,
        memory=base.memory, conversation_memory_max_items=base.conversation_memory_max_items,
        conversation_memory_max_characters=base.conversation_memory_max_characters,
        checkpoint_generations=base.retention_policy.checkpoint_generations,
    )
    values = {field.name: getattr(base, field.name) for field in fields(Stage15System)}
    values["text_input_runner"] = runner
    return Stage16System(**values)
