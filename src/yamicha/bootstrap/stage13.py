"""Compose stage 13's bounded conversation-derived long-term memory."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, fields
from typing import ClassVar
from uuid import uuid4

from yamicha.contracts import (
    AuxiliaryIntelligenceProposal,
    AuxiliaryIntelligenceResult,
    ConversationMemoryCandidate,
    ConversationMemoryForgetRecord,
    ConversationMemoryForgetRequest,
    ConversationMemoryInterpretation,
    ConversationMemoryReview,
    ConversationMemoryWindow,
    DialogueOutput,
    ExternalTime,
    IntegratedIntelligenceRequest,
    IntelligenceAdoption,
    IntelligenceCandidateReview,
    IntelligenceResultStatus,
    JudgmentContext,
    JudgmentFinalization,
    LifecycleRecord,
    RawTextInput,
    SensoryEvent,
    Stage10InputOutcome,
    Stage13InputOutcome,
)
from yamicha.life.stage13 import Stage13Core, Stage13Judgment, Stage13Memory

from .composition import YamichaComposition
from .stage12 import (
    Stage12System,
    Stage12TextInputRunner,
    make_stage12_system,
)


CONFIGURATION_VERSION = "stage13-v1"
DEFAULT_CONVERSATION_MEMORY_MAX_ITEMS = 5
DEFAULT_CONVERSATION_MEMORY_MAX_CHARACTERS = 1024


class Stage13TextInputRunner(Stage12TextInputRunner):
    def __init__(
        self,
        *,
        memory: Stage13Memory,
        core: Stage13Core,
        judgment: Stage13Judgment,
        conversation_memory_max_items: int,
        conversation_memory_max_characters: int,
        **kwargs: object,
    ) -> None:
        if conversation_memory_max_items <= 0:
            raise ValueError("conversation-memory item limit must be positive")
        if conversation_memory_max_characters < 0:
            raise ValueError("conversation-memory character limit must not be negative")
        super().__init__(core=core, judgment=judgment, **kwargs)
        self._stage13_memory = memory
        self._stage13_core = core
        self._stage13_judgment = judgment
        self._conversation_memory_max_items = conversation_memory_max_items
        self._conversation_memory_max_characters = conversation_memory_max_characters
        self._current_memory_window: ConversationMemoryWindow | None = None
        self._current_memory_candidate: ConversationMemoryCandidate | None = None
        self._current_memory_review: ConversationMemoryReview | None = None
        self._current_memory_forget_request: (
            ConversationMemoryForgetRequest | None
        ) = None
        self._current_memory_forget_record: (
            ConversationMemoryForgetRecord | None
        ) = None
        self._memory_interpretation_request: IntegratedIntelligenceRequest | None = None
        self._memory_interpretation_result: AuxiliaryIntelligenceResult | None = None
        self._memory_interpretation_review: IntelligenceCandidateReview | None = None
        self._memory_interpretation_adoption: IntelligenceAdoption | None = None
        self._conversation_memory_interpretation: (
            ConversationMemoryInterpretation | None
        ) = None

    def run(self, raw: RawTextInput) -> Stage13InputOutcome:
        self._current_memory_window = None
        self._current_memory_candidate = None
        self._current_memory_review = None
        self._current_memory_forget_request = None
        self._current_memory_forget_record = None
        self._memory_interpretation_request = None
        self._memory_interpretation_result = None
        self._memory_interpretation_review = None
        self._memory_interpretation_adoption = None
        self._conversation_memory_interpretation = None
        base = super().run(raw)
        values = {
            field.name: getattr(base, field.name)
            for field in fields(Stage10InputOutcome)
        }
        return Stage13InputOutcome(
            **values,
            conversation_memory_window=self._current_memory_window,
            conversation_memory_candidate=self._current_memory_candidate,
            conversation_memory_review=self._current_memory_review,
            conversation_memory_forget_request=self._current_memory_forget_request,
            conversation_memory_forget_record=self._current_memory_forget_record,
            memory_interpretation_request=self._memory_interpretation_request,
            memory_interpretation_result=self._memory_interpretation_result,
            memory_interpretation_review=self._memory_interpretation_review,
            memory_interpretation_adoption=self._memory_interpretation_adoption,
            conversation_memory_interpretation=(
                self._conversation_memory_interpretation
            ),
        )

    def _prepare_memory_context(self, event: SensoryEvent) -> None:
        remaining_after_current = max(
            0,
            self._intelligence_constraints.max_input_characters
            - len(event.meaning.normalized_text),
        )
        self._current_memory_window = self._stage13_memory.prepare_selection(
            event,
            max_items=self._conversation_memory_max_items,
            max_characters=min(
                self._conversation_memory_max_characters,
                remaining_after_current,
            ),
        )

    def _propose_dialogue_assistance(
        self,
        context: JudgmentContext,
        event: SensoryEvent,
        proposed_at: ExternalTime,
    ) -> AuxiliaryIntelligenceProposal:
        memory_characters = (
            0
            if self._current_memory_window is None
            else self._current_memory_window.total_characters
        )
        dialogue_window = self._stage12_relationship.select_dialogue_window(
            event,
            max_exchanges=self._dialogue_max_exchanges,
            max_characters=(
                self._intelligence_constraints.max_input_characters
                - memory_characters
            ),
            excluded_lifecycle_ids=tuple(self._stage13_core.dependent_dialogue_lifecycles(
                self._stage13_core.conversation_memory_dialogue_exclusions(self._current_memory_window)
            )),
        )
        self._last_dialogue_window = dialogue_window
        return self._stage13_judgment.propose_dialogue_assistance(
            lifecycle_id=context.lifecycle_id,
            model=self._intelligence_model,
            input_text=event.meaning.normalized_text,
            input_source_reference=event.event_id,
            constraints=self._intelligence_constraints,
            proposed_at=proposed_at,
            dialogue_context=dialogue_window,
            conversation_memory=self._current_memory_window,
        )

    def _record_long_term_memory(
        self,
        event: SensoryEvent,
        dialogue_output: DialogueOutput,
        record: LifecycleRecord,
        finalization: JudgmentFinalization,
        intelligence_result: AuxiliaryIntelligenceResult | None,
    ) -> None:
        del dialogue_output, record
        context = self._stage12_relationship.active_dialogue_context
        if context is None:
            raise RuntimeError("completed dialogue has no active context")
        forget_request = self._stage13_judgment.propose_conversation_memory_forgetting(
            event,
            self._current_memory_window,
        )
        if forget_request is not None:
            self._current_memory_forget_request = forget_request
            self._current_memory_forget_record = (
                self._stage13_core.route_conversation_memory_forgetting(forget_request)
            )
            return
        candidate = self._stage13_judgment.propose_conversation_memory(
            event,
            context.turns,
        )
        if candidate is not None:
            self._adopt_conversation_memory_candidate(candidate)
            return
        if not self._stage13_judgment.should_request_memory_interpretation(
            event.meaning.normalized_text
        ) or (
            intelligence_result is None
            or intelligence_result.status is not IntelligenceResultStatus.SUCCESS
        ):
            return
        interpretation = self._request_memory_interpretation(
            event,
            finalization,
        )
        if interpretation is None:
            return
        forget_request = self._stage13_judgment.propose_conversation_memory_forgetting(
            event,
            self._current_memory_window,
            interpretation,
        )
        if forget_request is not None:
            self._current_memory_forget_request = forget_request
            self._current_memory_forget_record = (
                self._stage13_core.route_conversation_memory_forgetting(forget_request)
            )
            return
        candidate = self._stage13_judgment.propose_conversation_memory(
            event,
            context.turns,
            interpretation,
        )
        if candidate is not None:
            self._adopt_conversation_memory_candidate(candidate)

    def _request_memory_interpretation(
        self,
        event: SensoryEvent,
        finalization: JudgmentFinalization,
    ) -> ConversationMemoryInterpretation | None:
        proposal = self._stage13_judgment.propose_memory_interpretation(
            lifecycle_id=event.correlation_id,
            model=self._intelligence_model,
            input_text=event.meaning.normalized_text,
            input_source_reference=event.event_id,
            constraints=self._intelligence_constraints,
            proposed_at=event.received_at,
            dialogue_context=self._last_dialogue_window,
            conversation_memory=self._current_memory_window,
        )
        request = self._stage13_core.integrate_intelligence_request(
            proposal,
            finalization,
            event.received_at,
        )
        result = self._stage10_auxiliary.generate_candidate(
            request,
            event.received_at,
        )
        review = self._stage13_judgment.review_intelligence_result(
            result,
            event.received_at,
        )
        adoption = self._stage13_core.finalize_intelligence_adoption(
            request=request,
            result=result,
            review=review,
            finalization=finalization,
            decided_at=event.received_at,
        )
        self._memory_interpretation_request = request
        self._memory_interpretation_result = result
        self._memory_interpretation_review = review
        self._memory_interpretation_adoption = adoption
        self._store_trace(request, result, adoption, event.received_at)
        interpretation = self._stage13_judgment.interpret_adopted_memory_candidate(
            event=event,
            result=result,
            adoption=adoption,
        )
        self._conversation_memory_interpretation = interpretation
        return interpretation

    def _adopt_conversation_memory_candidate(
        self,
        candidate: ConversationMemoryCandidate,
    ) -> None:
        self._current_memory_candidate = candidate
        self._current_memory_review = (
            self._stage13_core.route_conversation_memory_candidate(candidate)
        )


@dataclass(slots=True, kw_only=True)
class Stage13System(Stage12System):
    stage_label: ClassVar[int] = 13
    memory: Stage13Memory
    core: Stage13Core
    judgment: Stage13Judgment
    text_input_runner: Stage13TextInputRunner
    conversation_memory_max_items: int
    conversation_memory_max_characters: int


def make_stage13_system(
    *,
    conversation_memory_max_items: int = DEFAULT_CONVERSATION_MEMORY_MAX_ITEMS,
    conversation_memory_max_characters: int = (
        DEFAULT_CONVERSATION_MEMORY_MAX_CHARACTERS
    ),
    conversation_memory_candidate_id_factory: Callable[[], str] | None = None,
    conversation_memory_review_id_factory: Callable[[], str] | None = None,
    conversation_memory_item_id_factory: Callable[[], str] | None = None,
    conversation_memory_forget_request_id_factory: Callable[[], str] | None = None,
    conversation_memory_forget_record_id_factory: Callable[[], str] | None = None,
    _configuration_version: str = CONFIGURATION_VERSION,
    _upgrade_from_configuration_versions: tuple[str, ...] = ("stage12-v1",),
    _memory_factory: type[Stage13Memory] = Stage13Memory,
    _memory_options: dict[str, object] | None = None,
    _core_factory: type[Stage13Core] = Stage13Core,
    _judgment_factory: type[Stage13Judgment] = Stage13Judgment,
    _judgment_options: dict[str, object] | None = None,
    _relationship_factory=None,
    _intelligence_allowed_input_scope: tuple[str, ...] = (
        "current_verified_text",
        "verified_recent_dialogue_turns",
        "selected_adopted_conversation_memories",
        "verified_speaker_and_model_identity",
    ),
    **stage12_options: object,
) -> Stage13System:
    if conversation_memory_max_items <= 0:
        raise ValueError("conversation-memory item limit must be positive")
    if conversation_memory_max_characters < 0:
        raise ValueError("conversation-memory character limit must not be negative")
    base = make_stage12_system(
        _configuration_version=_configuration_version,
        _upgrade_from_configuration_versions=_upgrade_from_configuration_versions,
        _intelligence_allowed_input_scope=_intelligence_allowed_input_scope,
        _memory_factory=_memory_factory,
        _memory_options={
            "conversation_review_id_factory": conversation_memory_review_id_factory,
            "conversation_memory_item_id_factory": (
                conversation_memory_item_id_factory
            ),
            "conversation_forget_record_id_factory": (
                conversation_memory_forget_record_id_factory
            ),
            **(_memory_options or {}),
        },
        _core_factory=_core_factory,
        _judgment_factory=_judgment_factory,
        _judgment_options={
            "conversation_memory_candidate_id_factory": (
                conversation_memory_candidate_id_factory
            ),
            "conversation_memory_forget_request_id_factory": (
                conversation_memory_forget_request_id_factory
            ),
            **(_judgment_options or {}),
        },
        **(
            {}
            if _relationship_factory is None
            else {"_relationship_factory": _relationship_factory}
        ),
        **stage12_options,
    )
    memory = base.memory
    core = base.core
    judgment = base.judgment
    if not isinstance(memory, Stage13Memory):
        raise TypeError("stage-13 Memory was not composed")
    if not isinstance(core, Stage13Core):
        raise TypeError("stage-13 Core was not composed")
    if not isinstance(judgment, Stage13Judgment):
        raise TypeError("stage-13 Judgment was not composed")
    runner = Stage13TextInputRunner(
        protection_boundary=base.protection_boundary,
        sensation=base.sensation,
        core=core,
        judgment=judgment,
        language=base.language,
        auxiliary_intelligence=base.auxiliary_intelligence,
        intelligence_model=base.intelligence_model,
        intelligence_constraints=base.intelligence_constraints,
        correlation_id_factory=stage12_options.get("input_correlation_id_factory")
        or (lambda: str(uuid4())),
        trace_id_factory=stage12_options.get("intelligence_trace_id_factory"),
        persistence_store=base.persistence,
        snapshot_coordinator=base.snapshot_coordinator,
        relationship=base.relationship,
        dialogue_max_exchanges=base.dialogue_max_exchanges,
        memory=memory,
        conversation_memory_max_items=conversation_memory_max_items,
        conversation_memory_max_characters=conversation_memory_max_characters,
    )
    composition = YamichaComposition(
        core=core,
        memory=memory,
        state=base.state,
        sensation=base.sensation,
        judgment=judgment,
        relationship=base.relationship,
        capability=base.capability,
        language=base.language,
        auxiliary_intelligence=base.auxiliary_intelligence,
        runtime=base.runtime,
        protection_boundary=base.protection_boundary,
        external_effect_gate=base.external_effect_gate,
    )
    values = {field.name: getattr(base, field.name) for field in fields(Stage12System)}
    values["composition"] = composition
    values["text_input_runner"] = runner
    return Stage13System(
        **values,
        conversation_memory_max_items=conversation_memory_max_items,
        conversation_memory_max_characters=conversation_memory_max_characters,
    )
