"""Compose stage 14's governed correction, forgetting, and deletion path."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, fields
from typing import ClassVar
from uuid import uuid4

from yamicha.body.persistence import PersistenceError
from yamicha.contracts import (
    AuxiliaryIntelligenceResult,
    ConversationMemoryGovernanceRecord,
    ConversationMemoryGovernanceRequest,
    ConversationMemoryItem,
    ConversationMemoryOperationKind,
    ConversationMemoryOperationStatus,
    ConversationMemoryPendingConfirmation,
    CycleOutcome,
    DialogueContext,
    DialogueOutput,
    ExternalTime,
    JudgmentFinalization,
    LifecycleRecord,
    RawTextInput,
    SensoryEvent,
    Stage13InputOutcome,
    Stage14InputOutcome,
)
from yamicha.life.stage14 import (
    Stage14Core,
    Stage14Judgment,
    Stage14Memory,
    Stage14Relationship,
)

from .composition import YamichaComposition
from .stage13 import Stage13System, Stage13TextInputRunner, make_stage13_system


CONFIGURATION_VERSION = "stage14-v1"


@dataclass(frozen=True, slots=True)
class Stage14RetentionPolicy:
    active_dialogue_contexts: int = 1
    checkpoint_generations: int = 2
    lifecycle_evidence_deletable: bool = False
    protection_audit_deletable: bool = False
    conversation_content_deletable: bool = True

    def __post_init__(self) -> None:
        if self.active_dialogue_contexts != 1 or self.checkpoint_generations < 1:
            raise ValueError("stage-14 retention policy is invalid")


class Stage14TextInputRunner(Stage13TextInputRunner):
    def __init__(
        self,
        *,
        memory: Stage14Memory,
        core: Stage14Core,
        judgment: Stage14Judgment,
        relationship: Stage14Relationship,
        checkpoint_generations: int,
        **kwargs: object,
    ) -> None:
        super().__init__(
            memory=memory,
            core=core,
            judgment=judgment,
            relationship=relationship,
            **kwargs,
        )
        self._stage14_memory = memory
        self._stage14_core = core
        self._stage14_judgment = judgment
        self._stage14_relationship = relationship
        self._checkpoint_generations = checkpoint_generations
        self._governance_request: ConversationMemoryGovernanceRequest | None = None
        self._governance_record: ConversationMemoryGovernanceRecord | None = None
        self._pending_confirmation: ConversationMemoryPendingConfirmation | None = None

    def run(self, raw: RawTextInput) -> Stage14InputOutcome:
        self._governance_request = None
        self._governance_record = None
        self._pending_confirmation = None
        base = super().run(raw)
        if (
            self._governance_record is not None
            and self._governance_record.operation
            is ConversationMemoryOperationKind.PHYSICAL_DELETE
            and self._governance_record.status
            is ConversationMemoryOperationStatus.PARTIAL
        ):
            try:
                self._persistence_store.prune_checkpoints(keep_latest=1)
            except PersistenceError:
                pass
            else:
                try:
                    self._persistence_store.compact_storage()
                except PersistenceError:
                    self._governance_record = (
                        self._stage14_memory.update_physical_deletion_residual(
                            self._governance_record.record_id,
                            ("sqlite_storage_compaction_unconfirmed",),
                        )
                    )
                    self._commit_governance_snapshot(raw.received_at)
                else:
                    self._governance_record = (
                        self._stage14_memory.finalize_physical_deletion(
                            self._governance_record.record_id
                        )
                    )
                    self._commit_governance_snapshot(raw.received_at)
        try:
            self._persistence_store.prune_checkpoints(
                keep_latest=self._checkpoint_generations
            )
        except PersistenceError:
            # Pruning is bounded maintenance; the already committed newest
            # checkpoint remains the recoverable canonical state.
            pass
        values = {
            field.name: getattr(base, field.name)
            for field in fields(Stage13InputOutcome)
        }
        return Stage14InputOutcome(
            **values,
            conversation_memory_governance_request=self._governance_request,
            conversation_memory_governance_record=self._governance_record,
            conversation_memory_pending_confirmation=self._pending_confirmation,
        )

    def _commit_governance_snapshot(self, created_at: ExternalTime) -> None:
        try:
            self._stage10_snapshot_coordinator.commit(created_at)
        except Exception:
            self.mark_persistence_unhealthy()
            raise

    def _record_long_term_memory(
        self,
        event: SensoryEvent,
        dialogue_output: DialogueOutput,
        record: LifecycleRecord,
        finalization: JudgmentFinalization,
        intelligence_result: AuxiliaryIntelligenceResult | None,
    ) -> None:
        pending = self._stage14_memory.pending_confirmation_for(event.source_id)
        if pending is not None and self._stage14_judgment.is_confirmation(
            event.meaning.normalized_text
        ):
            source_references = self._target_source_references(
                pending.request.target_items
            )
            self._governance_request, self._governance_record = (
                self._stage14_memory.confirm_pending_physical_deletion(
                    counterpart_id=event.source_id,
                    lifecycle_id=event.correlation_id,
                    source_reference=event.event_id,
                    confirmed_at=event.received_at,
                )
            )
            self._stage14_relationship.redact_active_dialogue(
                source_references=source_references,
                lifecycle_ids=(
                    pending.request.lifecycle_id,
                    event.correlation_id,
                ),
            )
            return
        if pending is not None and self._stage14_judgment.is_cancellation(
            event.meaning.normalized_text
        ):
            self._governance_request, self._governance_record = (
                self._stage14_memory.cancel_pending_confirmation(
                    counterpart_id=event.source_id,
                    lifecycle_id=event.correlation_id,
                    source_reference=event.event_id,
                    cancelled_at=event.received_at,
                )
            )
            return
        if pending is not None:
            self._pending_confirmation = pending
            return
        governance_window = self._stage14_memory.prepare_governance_selection(
            event
        )
        request = self._stage14_judgment.propose_memory_governance(
            event,
            governance_window,
        )
        if request is not None:
            self._governance_request = request
            self._governance_record = self._stage14_core.route_memory_governance(
                request
            )
            if (
                self._governance_record.status
                is ConversationMemoryOperationStatus.CONFIRMATION_REQUIRED
                and request.operation
                is ConversationMemoryOperationKind.PHYSICAL_DELETE
            ):
                self._pending_confirmation = (
                    self._stage14_memory.pending_confirmation_for(event.source_id)
                )
            return
        explicit_forget = (
            self._stage14_judgment.propose_conversation_memory_forgetting(
                event,
                governance_window,
            )
        )
        if explicit_forget is not None:
            self._current_memory_forget_request = explicit_forget
            self._current_memory_forget_record = (
                self._stage14_core.route_conversation_memory_forgetting(
                    explicit_forget
                )
            )
            self._governance_record = (
                self._stage14_memory.record_logical_forgetting(
                    self._current_memory_forget_record
                )
            )
            self._governance_request = ConversationMemoryGovernanceRequest(
                request_id=explicit_forget.request_id,
                lifecycle_id=explicit_forget.lifecycle_id,
                counterpart_id=explicit_forget.counterpart_id,
                operation=ConversationMemoryOperationKind.LOGICAL_FORGET,
                target_items=explicit_forget.target_items,
                source_reference=explicit_forget.source_reference,
                reason=explicit_forget.reason,
                requested_at=explicit_forget.requested_at,
            )
            return
        super()._record_long_term_memory(
            event,
            dialogue_output,
            record,
            finalization,
            intelligence_result,
        )
        if self._current_memory_forget_record is not None:
            self._governance_record = (
                self._stage14_memory.record_logical_forgetting(
                    self._current_memory_forget_record
                )
            )
            forgotten = self._current_memory_forget_request
            assert forgotten is not None
            self._governance_request = ConversationMemoryGovernanceRequest(
                request_id=forgotten.request_id,
                lifecycle_id=forgotten.lifecycle_id,
                counterpart_id=forgotten.counterpart_id,
                operation=ConversationMemoryOperationKind.LOGICAL_FORGET,
                target_items=forgotten.target_items,
                source_reference=forgotten.source_reference,
                reason=forgotten.reason,
                requested_at=forgotten.requested_at,
            )
            return
        unmatched = self._stage14_judgment.propose_unmatched_logical_forgetting(
            event
        )
        if unmatched is not None:
            self._governance_request = unmatched
            self._governance_record = self._stage14_core.route_memory_governance(
                unmatched
            )

    def _target_source_references(
        self,
        target_items: tuple[tuple[str, int], ...],
    ) -> tuple[str, ...]:
        target_ids = {item_id for item_id, _ in target_items}
        return tuple(
            dict.fromkeys(
                reference
                for item in self._stage14_memory.conversation_memory_items
                if item.memory_item_id in target_ids
                for reference in item.source_references
            )
        )


@dataclass(slots=True, kw_only=True)
class Stage14System(Stage13System):
    stage_label: ClassVar[int] = 14
    memory: Stage14Memory
    core: Stage14Core
    judgment: Stage14Judgment
    relationship: Stage14Relationship
    text_input_runner: Stage14TextInputRunner
    retention_policy: Stage14RetentionPolicy

    def list_conversation_memories(
        self,
        counterpart_id: str,
        *,
        include_inactive: bool = False,
    ) -> tuple[ConversationMemoryItem, ...]:
        return self.memory.visible_conversation_memories(
            counterpart_id,
            include_inactive=include_inactive,
        )

    def run_time_cycle(self) -> CycleOutcome:
        outcome = Stage13System.run_time_cycle(self)
        self._prune_checkpoint_generations()
        return outcome

    def start_new_dialogue_context(self, started_at: ExternalTime) -> DialogueContext:
        context = Stage13System.start_new_dialogue_context(self, started_at)
        self._prune_checkpoint_generations()
        return context

    def _prune_checkpoint_generations(self) -> None:
        try:
            self.persistence.prune_checkpoints(
                keep_latest=self.retention_policy.checkpoint_generations
            )
        except PersistenceError:
            pass


def make_stage14_system(
    *,
    retention_policy: Stage14RetentionPolicy | None = None,
    conversation_memory_governance_request_id_factory: Callable[[], str] | None = None,
    conversation_memory_governance_record_id_factory: Callable[[], str] | None = None,
    conversation_memory_confirmation_id_factory: Callable[[], str] | None = None,
    _configuration_version: str = CONFIGURATION_VERSION,
    _upgrade_from_configuration_versions: tuple[str, ...] = ("stage13-v1",),
    _core_factory: type[Stage14Core] = Stage14Core,
    _judgment_factory: type[Stage14Judgment] = Stage14Judgment,
    _relationship_factory: type[Stage14Relationship] = Stage14Relationship,
    _memory_factory: type[Stage14Memory] = Stage14Memory,
    **stage13_options: object,
) -> Stage14System:
    policy = retention_policy or Stage14RetentionPolicy()
    base = make_stage13_system(
        _configuration_version=_configuration_version,
        _upgrade_from_configuration_versions=_upgrade_from_configuration_versions,
        _memory_factory=_memory_factory,
        _memory_options={
            "conversation_governance_record_id_factory": (
                conversation_memory_governance_record_id_factory
            ),
            "conversation_confirmation_id_factory": (
                conversation_memory_confirmation_id_factory
            ),
        },
        _core_factory=_core_factory,
        _judgment_factory=_judgment_factory,
        _judgment_options={
            "conversation_memory_governance_request_id_factory": (
                conversation_memory_governance_request_id_factory
            ),
        },
        _relationship_factory=_relationship_factory,
        **stage13_options,
    )
    memory = base.memory
    core = base.core
    judgment = base.judgment
    relationship = base.relationship
    if not isinstance(memory, Stage14Memory):
        raise TypeError("stage-14 Memory was not composed")
    if not isinstance(core, Stage14Core):
        raise TypeError("stage-14 Core was not composed")
    if not isinstance(judgment, Stage14Judgment):
        raise TypeError("stage-14 Judgment was not composed")
    if not isinstance(relationship, Stage14Relationship):
        raise TypeError("stage-14 Relationship was not composed")
    runner = Stage14TextInputRunner(
        protection_boundary=base.protection_boundary,
        sensation=base.sensation,
        core=core,
        judgment=judgment,
        language=base.language,
        auxiliary_intelligence=base.auxiliary_intelligence,
        intelligence_model=base.intelligence_model,
        intelligence_constraints=base.intelligence_constraints,
        correlation_id_factory=stage13_options.get("input_correlation_id_factory")
        or (lambda: str(uuid4())),
        trace_id_factory=stage13_options.get("intelligence_trace_id_factory"),
        persistence_store=base.persistence,
        snapshot_coordinator=base.snapshot_coordinator,
        relationship=relationship,
        dialogue_max_exchanges=base.dialogue_max_exchanges,
        memory=memory,
        conversation_memory_max_items=base.conversation_memory_max_items,
        conversation_memory_max_characters=base.conversation_memory_max_characters,
        checkpoint_generations=policy.checkpoint_generations,
    )
    composition = YamichaComposition(
        core=core,
        memory=memory,
        state=base.state,
        sensation=base.sensation,
        judgment=judgment,
        relationship=relationship,
        capability=base.capability,
        language=base.language,
        auxiliary_intelligence=base.auxiliary_intelligence,
        runtime=base.runtime,
        protection_boundary=base.protection_boundary,
        external_effect_gate=base.external_effect_gate,
    )
    values = {field.name: getattr(base, field.name) for field in fields(Stage13System)}
    values["composition"] = composition
    values["text_input_runner"] = runner
    return Stage14System(
        **values,
        retention_policy=policy,
    )
