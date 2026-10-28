"""Core routing for stage-14 governed memory operations."""

from __future__ import annotations

from dataclasses import replace

from yamicha.contracts import (
    ConversationMemoryGovernanceRecord,
    ConversationMemoryGovernanceRequest,
    RecordKind,
    ResponsibilityId,
)
from yamicha.life.stage13 import Stage13Core

from .memory import Stage14Memory


class Stage14Core(Stage13Core):
    def conversation_memory_deletion_lifecycles(self, item_ids: set[str]) -> set[str]:
        memory = self._stage6_memory
        if not isinstance(memory, Stage14Memory):
            raise TypeError("stage-14 governed memory was not composed")
        lifecycles = super().conversation_memory_deletion_lifecycles(item_ids)
        # Governance can update a memory too large for the normal model-input
        # window. Its source turn still owns deletable content, even when no
        # conversation-memory usage entry could be recorded for that turn.
        lifecycles.update(
            record.lifecycle_id
            for record in memory.conversation_memory_governance_records
            if record.replaces_item_id in item_ids
            or any(item_id in item_ids for item_id, _, _ in record.affected_items)
        )
        prefixes = tuple(f"memory-governance-target:{item_id}:v" for item_id in item_ids)
        lifecycles.update(
            record.lifecycle_id for record in self._records
            if any(entry.kind is RecordKind.JUDGMENT
                   and entry.source_owner is ResponsibilityId.MEMORY
                   and entry.source_reference.startswith(prefixes)
                   for entry in record.entries)
        )
        return self.dependent_dialogue_lifecycles(lifecycles)

    def route_memory_governance(
        self,
        request: ConversationMemoryGovernanceRequest,
    ) -> ConversationMemoryGovernanceRecord:
        memory = self._stage6_memory
        if not isinstance(memory, Stage14Memory):
            raise TypeError("stage-14 governed memory was not composed")
        result = memory.apply_governance(request)
        # Pending or cancelled requests do not change an item version. Retain
        # their target references separately so a later deletion can still
        # redact the original request without retaining its text in the audit.
        if request.target_items:
            for index, record in enumerate(self._records):
                if record.lifecycle_id == request.lifecycle_id:
                    entries = tuple(self._entry(
                        lifecycle_id=request.lifecycle_id,
                        kind=RecordKind.JUDGMENT,
                        owner=ResponsibilityId.MEMORY,
                        reference=f"memory-governance-target:{item_id}:v{version}",
                        summary=f"governance request targets memory: {request.operation.value}",
                        occurred_at=request.requested_at,
                    ) for item_id, version in request.target_items)
                    self._records[index] = replace(record, entries=(*record.entries, *entries))
                    break
        return result
