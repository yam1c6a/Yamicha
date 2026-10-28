"""Core routing for stage-13 conversation-memory candidates."""

from __future__ import annotations

from dataclasses import replace

from yamicha.contracts import (
    AuxiliaryIntelligenceProposal, IntegratedIntelligenceRequest,
    ConversationMemoryCandidate,
    ConversationMemoryForgetRecord,
    ConversationMemoryForgetRequest,
    ConversationMemoryReview,
    ConversationMemoryWindow, DialogueOutput, ExpressionArtifact, ExpressionReview,
    ExternalTime, IntelligencePurpose, JudgmentContext, JudgmentFinalization, JudgmentResult,
    LifecycleRecord, RecordKind, ResponsibilityId,
)
from yamicha.life.stage10 import Stage10Core

from .memory import Stage13Memory


class Stage13Core(Stage10Core):
    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._dialogue_dependencies: dict[str, tuple[str, ...]] = {}

    def integrate_intelligence_request(
        self, proposal: AuxiliaryIntelligenceProposal,
        finalization: JudgmentFinalization, integrated_at: ExternalTime,
    ) -> IntegratedIntelligenceRequest:
        request = super().integrate_intelligence_request(proposal, finalization, integrated_at)
        if proposal.purpose is not IntelligencePurpose.DIALOGUE_RESPONSE_CANDIDATE:
            return request
        window = proposal.dialogue_context
        self._dialogue_dependencies[proposal.lifecycle_id] = (() if window is None else
            tuple(dict.fromkeys(turn.lifecycle_id for turn in window.turns)))
        return request

    def record_completed_lifecycle(
        self, *, context: JudgmentContext, judgment: JudgmentResult,
        finalization: JudgmentFinalization, expression: ExpressionArtifact,
        expression_review: ExpressionReview, dialogue_output: DialogueOutput,
    ) -> LifecycleRecord:
        record = super().record_completed_lifecycle(
            context=context, judgment=judgment, finalization=finalization,
            expression=expression, expression_review=expression_review,
            dialogue_output=dialogue_output,
        )
        memory = self._stage6_memory
        assert isinstance(memory, Stage13Memory)
        window = memory.selected_window(context.lifecycle_id)
        entries = [self._entry(
            lifecycle_id=context.lifecycle_id, kind=RecordKind.JUDGMENT,
            owner=ResponsibilityId.CORE, reference="dialogue-materials:v1",
            summary="dialogue material dependencies were recorded without their content",
            occurred_at=context.event.received_at,
        )]
        for selection in (() if window is None else window.selections):
            entries.append(self._entry(
                lifecycle_id=context.lifecycle_id, kind=RecordKind.JUDGMENT,
                owner=ResponsibilityId.MEMORY,
                reference=f"conversation-memory:{selection.memory_item_id}:v{selection.memory_item_version}",
                summary=(f"counterpart-reported memory; certainty={selection.certainty.value}; "
                         f"selection={selection.selection_reason.split(':', 1)[0]}"),
                occurred_at=context.event.received_at,
            ))
        for dependency in self._dialogue_dependencies.pop(context.lifecycle_id, ()):
            entries.append(self._entry(
                lifecycle_id=context.lifecycle_id, kind=RecordKind.JUDGMENT,
                owner=ResponsibilityId.RELATIONSHIP, reference=f"dialogue-turn:{dependency}",
                summary="prior dialogue exchange was supplied as untrusted context",
                occurred_at=context.event.received_at,
            ))
        updated = replace(record, entries=(*record.entries, *entries))
        self._records[-1] = updated
        return updated

    def dependent_dialogue_lifecycles(self, excluded: set[str]) -> set[str]:
        """Close exclusions over actual dialogue inputs, including indirect reuse.

        Legacy exchanges lack a complete material manifest. Keep them in the
        stored context, but do not supply untraceable content to a new model call.
        """
        excluded = set(excluded)
        for record in self._records:
            if not any(entry.source_owner is ResponsibilityId.CORE
                       and entry.source_reference == "dialogue-materials:v1"
                       for entry in record.entries):
                excluded.add(record.lifecycle_id)
        dependents: dict[str, set[str]] = {}
        for record in self._records:
            for entry in record.entries:
                if (entry.kind is RecordKind.JUDGMENT
                        and entry.source_owner is ResponsibilityId.RELATIONSHIP
                        and entry.source_reference.startswith("dialogue-turn:")):
                    source = entry.source_reference.removeprefix("dialogue-turn:")
                    dependents.setdefault(source, set()).add(record.lifecycle_id)
        pending = list(excluded)
        while pending:
            for dependent in dependents.get(pending.pop(), set()) - excluded:
                excluded.add(dependent)
                pending.append(dependent)
        return excluded

    def conversation_memory_dialogue_exclusions(
        self, window: ConversationMemoryWindow | None, *, exclude_unselected: bool = True,
    ) -> set[str]:
        memory = self._stage6_memory
        assert isinstance(memory, Stage13Memory)
        selected = set() if window is None else {
            f"conversation-memory:{item.memory_item_id}:v{item.memory_item_version}"
            for item in window.selections
        }
        if not exclude_unselected:
            selected = {
                f"conversation-memory:{item.memory_item_id}:v{item.version}"
                for item in memory.conversation_memory_items if item.active
            }
        invalid_sources = {
            source for item in memory.conversation_memory_items
            if (not item.active or item.version > 1
                or f"conversation-memory:{item.memory_item_id}:v{item.version}" not in selected)
            for source in item.source_references
        }
        return {
            record.lifecycle_id for record in self._records
            if any(entry.source_reference in invalid_sources or (
                entry.kind is RecordKind.JUDGMENT
                and entry.source_owner is ResponsibilityId.MEMORY
                and entry.source_reference.startswith("conversation-memory:")
                and entry.source_reference not in selected
            ) for entry in record.entries)
        }

    def conversation_memory_deletion_lifecycles(self, item_ids: set[str]) -> set[str]:
        memory = self._stage6_memory
        assert isinstance(memory, Stage13Memory)
        sources = {source for item in memory.conversation_memory_items
                   if item.memory_item_id in item_ids for source in item.source_references}
        prefixes = tuple(f"conversation-memory:{item_id}:v" for item_id in item_ids)
        direct = {
            record.lifecycle_id for record in self._records
            if any(entry.source_reference in sources or (
                entry.source_owner is ResponsibilityId.MEMORY
                and entry.source_reference.startswith(prefixes)
            ) for entry in record.entries)
        }
        return self.dependent_dialogue_lifecycles(direct)

    def route_conversation_memory_candidate(
        self,
        candidate: ConversationMemoryCandidate,
    ) -> ConversationMemoryReview:
        memory = self._stage6_memory
        if not isinstance(memory, Stage13Memory):
            raise TypeError("stage-13 conversation memory was not composed")
        return memory.review_conversation_candidate(candidate)

    def route_conversation_memory_forgetting(
        self,
        request: ConversationMemoryForgetRequest,
    ) -> ConversationMemoryForgetRecord:
        memory = self._stage6_memory
        if not isinstance(memory, Stage13Memory):
            raise TypeError("stage-13 conversation memory was not composed")
        return memory.forget_conversation_memory(request)
