"""Memory-owned stage-14 governance, redaction, and audit metadata."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import replace
from uuid import uuid4

from yamicha.contracts import (
    ConversationMemoryForgetRecord,
    ConversationMemoryGovernanceRecord,
    ConversationMemoryGovernanceRequest,
    ConversationMemoryItem,
    ConversationMemoryOperationKind,
    ConversationMemoryOperationStatus,
    ConversationMemoryPendingConfirmation,
    ConversationMemorySelection,
    ConversationMemoryWindow,
    ExternalTime,
    InformationCertainty,
    MemoryPersistenceSnapshot,
    SensoryEvent,
)
from yamicha.life.stage13 import Stage13Memory


REDACTED_MEMORY_MEANING = "[物理削除済み]"
CHECKPOINT_RESIDUAL_SCOPE = "older_sqlite_checkpoints"


class Stage14Memory(Stage13Memory):
    def __init__(
        self,
        *,
        conversation_governance_record_id_factory: Callable[[], str] | None = None,
        conversation_confirmation_id_factory: Callable[[], str] | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self._governance_record_id_factory = (
            conversation_governance_record_id_factory or (lambda: str(uuid4()))
        )
        self._confirmation_id_factory = (
            conversation_confirmation_id_factory or (lambda: str(uuid4()))
        )
        self._governance_records: list[ConversationMemoryGovernanceRecord] = []
        self._governance_record_ids: set[str] = set()
        self._pending_confirmations: dict[
            str, ConversationMemoryPendingConfirmation
        ] = {}
        self._confirmation_ids: set[str] = set()

    @property
    def conversation_memory_governance_records(
        self,
    ) -> tuple[ConversationMemoryGovernanceRecord, ...]:
        return tuple(self._governance_records)

    @property
    def conversation_memory_pending_confirmations(
        self,
    ) -> tuple[ConversationMemoryPendingConfirmation, ...]:
        return tuple(self._pending_confirmations.values())

    def pending_confirmation_for(
        self,
        counterpart_id: str,
    ) -> ConversationMemoryPendingConfirmation | None:
        return self._pending_confirmations.get(counterpart_id)

    def visible_conversation_memories(
        self,
        counterpart_id: str,
        *,
        include_inactive: bool = False,
    ) -> tuple[ConversationMemoryItem, ...]:
        return tuple(
            sorted(
                (
                    item
                    for item in self._conversation_items.values()
                    if item.counterpart_id == counterpart_id
                    and (include_inactive or item.active)
                ),
                key=lambda item: (
                    -item.updated_at.value.timestamp(),
                    item.memory_item_id,
                ),
            )
        )

    def prepare_governance_selection(
        self,
        event: SensoryEvent,
    ) -> ConversationMemoryWindow | None:
        """Resolve governance targets without the auxiliary-input character budget."""
        previous = self._prepared_window
        items = tuple(
            item
            for item in self._conversation_items.values()
            if item.counterpart_id == event.source_id and not item.physically_deleted
        )
        try:
            return self.prepare_selection(
                event,
                max_items=max(1, len(items)),
                max_characters=sum(len(item.meaning) for item in items),
            )
        finally:
            self._prepared_window = previous

    def prepare_selection(
        self,
        event: SensoryEvent,
        *,
        max_items: int,
        max_characters: int,
    ) -> ConversationMemoryWindow | None:
        window = super().prepare_selection(
            event,
            max_items=max_items,
            max_characters=max_characters,
        )
        text = event.meaning.normalized_text
        governance_markers = (
            "完全に",
            "じゃなくて",
            "訂正",
            "無効にして",
            "非活性にして",
            "確認済みにして",
        )
        if not any(marker in text for marker in governance_markers):
            return window
        target_text = text
        if "じゃなくて" in target_text:
            target_text = target_text.split("じゃなくて", 1)[0]
        if "→" in target_text:
            target_text = target_text.split("→", 1)[0]
        if "->" in target_text:
            target_text = target_text.split("->", 1)[0]
        target_text = target_text.removeprefix("訂正：").removeprefix("訂正:")
        query = self._normalized(target_text)
        for marker in (
            "完全に",
            "削除して",
            "削除",
            "忘れて",
            "忘却",
            "ください",
            "のこと",
            "ことを",
            "無効にして",
            "非活性にして",
            "確認済みにして",
        ):
            query = query.replace(self._normalized(marker), "")
        query = query.removesuffix(self._normalized("を"))
        fragments = self._fragments(query)
        matches = [
            item
            for item in self._conversation_items.values()
            if item.counterpart_id == event.source_id
            and not item.physically_deleted
            and (
                ("完全に" in text or item.active)
                and (
                    (query and query in self._normalized(item.meaning))
                    or bool(fragments & self._fragments(self._normalized(item.meaning)))
                )
            )
        ]
        matches.sort(
            key=lambda item: (
                -item.updated_at.value.timestamp(),
                item.memory_item_id,
            )
        )
        remaining = max_characters
        selections: list[ConversationMemorySelection] = []
        for item in matches:
            if len(selections) >= max_items or len(item.meaning) > remaining:
                continue
            selections.append(
                ConversationMemorySelection(
                    memory_item_id=item.memory_item_id,
                    memory_item_version=item.version,
                    counterpart_id=item.counterpart_id,
                    meaning=item.meaning,
                    source_references=item.source_references,
                    certainty=item.certainty,
                    content_trust=item.content_trust,
                    selection_reason="explicit physical deletion target",
                    updated_at=item.updated_at,
                )
            )
            remaining -= len(item.meaning)
        governed = ConversationMemoryWindow(
            memory_version=self._material_version,
            selections=tuple(selections),
            current_input_reference=event.event_id,
            max_items=max_items,
            max_characters=max_characters,
        )
        self._prepared_window = (event.correlation_id, governed)
        return governed

    def apply_governance(
        self,
        request: ConversationMemoryGovernanceRequest,
    ) -> ConversationMemoryGovernanceRecord:
        if not self._available:
            raise RuntimeError("conversation memory is unavailable")
        if request.operation is ConversationMemoryOperationKind.PHYSICAL_DELETE:
            if not request.target_items:
                return self._record_result(
                    request,
                    ConversationMemoryOperationStatus.NOT_FOUND,
                    reason="no matching memory was available for physical deletion",
                )
            if not request.confirmed:
                confirmation = ConversationMemoryPendingConfirmation(
                    confirmation_id=self._required_unique_id(
                        self._confirmation_id_factory,
                        self._confirmation_ids,
                        "conversation-memory confirmation",
                    ),
                    request=request,
                    created_at=request.requested_at,
                )
                self._pending_confirmations[request.counterpart_id] = confirmation
                return self._record_result(
                    request,
                    ConversationMemoryOperationStatus.CONFIRMATION_REQUIRED,
                    reason=(
                        "physical deletion is pending explicit counterpart confirmation"
                    ),
                )
            return self._physically_delete(request)

        targets = self._valid_targets(request)
        if not targets:
            return self._record_result(
                request,
                ConversationMemoryOperationStatus.NOT_FOUND,
                reason="no current memory matched the requested operation",
            )
        if (
            request.operation is ConversationMemoryOperationKind.CORRECTION
            and len(targets) != 1
        ):
            return self._record_result(
                request,
                ConversationMemoryOperationStatus.CONFIRMATION_REQUIRED,
                reason="correction target is ambiguous and must be specified more precisely",
            )
        if request.operation is ConversationMemoryOperationKind.CORRECTION:
            assert request.replacement_meaning is not None
            item = targets[0]
            updated = replace(
                item,
                version=item.version + 1,
                meaning=request.replacement_meaning,
                updated_at=request.requested_at,
                retention_reason=request.reason,
            )
            self._conversation_items[item.memory_item_id] = updated
            self._material_version += 1
            return self._record_result(
                request,
                ConversationMemoryOperationStatus.COMPLETED,
                affected=((item.memory_item_id, item.version, updated.version),),
                previous_meaning=item.meaning,
                replacement_meaning=updated.meaning,
                replaces_item_id=item.memory_item_id,
            )
        affected: list[tuple[str, int, int]] = []
        for item in targets:
            if request.operation is ConversationMemoryOperationKind.DEACTIVATION:
                updated = replace(
                    item,
                    version=item.version + 1,
                    updated_at=request.requested_at,
                    active=False,
                    deactivated_at=request.requested_at,
                    deactivation_reason=request.reason,
                )
            elif request.operation is ConversationMemoryOperationKind.REEVALUATION:
                assert request.replacement_certainty is not None
                updated = replace(
                    item,
                    version=item.version + 1,
                    updated_at=request.requested_at,
                    certainty=request.replacement_certainty,
                    retention_reason=request.reason,
                )
            else:
                raise ValueError("unsupported stage-14 governance operation")
            self._conversation_items[item.memory_item_id] = updated
            affected.append((item.memory_item_id, item.version, updated.version))
        self._material_version += 1
        return self._record_result(
            request,
            ConversationMemoryOperationStatus.COMPLETED,
            affected=tuple(affected),
        )

    def confirm_pending_physical_deletion(
        self,
        *,
        counterpart_id: str,
        lifecycle_id: str,
        source_reference: str,
        confirmed_at: ExternalTime,
    ) -> tuple[ConversationMemoryGovernanceRequest, ConversationMemoryGovernanceRecord]:
        pending = self._pending_confirmations.pop(counterpart_id)
        request = replace(
            pending.request,
            lifecycle_id=lifecycle_id,
            source_reference=source_reference,
            requested_at=confirmed_at,
            confirmed=True,
        )
        return request, self.apply_governance(request)

    def cancel_pending_confirmation(
        self,
        *,
        counterpart_id: str,
        lifecycle_id: str,
        source_reference: str,
        cancelled_at: ExternalTime,
    ) -> tuple[ConversationMemoryGovernanceRequest, ConversationMemoryGovernanceRecord]:
        pending = self._pending_confirmations.pop(counterpart_id)
        request = replace(
            pending.request,
            lifecycle_id=lifecycle_id,
            source_reference=source_reference,
            requested_at=cancelled_at,
        )
        return request, self._record_result(
            request,
            ConversationMemoryOperationStatus.CANCELLED,
            reason="physical deletion was cancelled by the counterpart",
        )

    def record_logical_forgetting(
        self,
        record: ConversationMemoryForgetRecord,
    ) -> ConversationMemoryGovernanceRecord:
        request = ConversationMemoryGovernanceRequest(
            request_id=record.request_id,
            lifecycle_id=record.lifecycle_id,
            counterpart_id=record.counterpart_id,
            operation=ConversationMemoryOperationKind.LOGICAL_FORGET,
            target_items=tuple(
                (item_id, old_version)
                for item_id, old_version, _ in record.deactivated_items
            ),
            source_reference=record.source_reference,
            reason=record.reason,
            requested_at=record.occurred_at,
        )
        return self._record_result(
            request,
            ConversationMemoryOperationStatus.COMPLETED,
            affected=record.deactivated_items,
        )

    def finalize_physical_deletion(
        self,
        record_id: str,
    ) -> ConversationMemoryGovernanceRecord:
        for index, record in enumerate(self._governance_records):
            if record.record_id == record_id:
                if (
                    record.operation is not ConversationMemoryOperationKind.PHYSICAL_DELETE
                    or record.status is not ConversationMemoryOperationStatus.PARTIAL
                ):
                    raise ValueError("physical deletion record cannot be finalized")
                completed = replace(
                    record,
                    status=ConversationMemoryOperationStatus.COMPLETED,
                    residual_scopes=(),
                )
                self._governance_records[index] = completed
                self._material_version += 1
                return completed
        raise ValueError("physical deletion record was not found")

    def update_physical_deletion_residual(
        self,
        record_id: str,
        residual_scopes: tuple[str, ...],
    ) -> ConversationMemoryGovernanceRecord:
        if not residual_scopes:
            raise ValueError("partial physical deletion requires residual scope")
        for index, record in enumerate(self._governance_records):
            if record.record_id == record_id:
                if (
                    record.operation is not ConversationMemoryOperationKind.PHYSICAL_DELETE
                    or record.status is not ConversationMemoryOperationStatus.PARTIAL
                ):
                    raise ValueError("physical deletion residual cannot be updated")
                updated = replace(record, residual_scopes=residual_scopes)
                self._governance_records[index] = updated
                self._material_version += 1
                return updated
        raise ValueError("physical deletion record was not found")

    def persistence_snapshot(self) -> MemoryPersistenceSnapshot:
        base = super().persistence_snapshot()
        return replace(
            base,
            conversation_governance_records=tuple(self._governance_records),
            conversation_pending_confirmations=tuple(
                self._pending_confirmations.values()
            ),
        )

    def restore_owned_information(self, snapshot: MemoryPersistenceSnapshot) -> None:
        if self._governance_records or self._pending_confirmations:
            raise RuntimeError("stage-14 Memory can only restore into a fresh owner")
        super().restore_owned_information(snapshot)
        self._governance_records.extend(snapshot.conversation_governance_records)
        self._governance_record_ids.update(
            record.record_id for record in snapshot.conversation_governance_records
        )
        for confirmation in snapshot.conversation_pending_confirmations:
            self._pending_confirmations[
                confirmation.request.counterpart_id
            ] = confirmation
            self._confirmation_ids.add(confirmation.confirmation_id)

    def _physically_delete(
        self,
        request: ConversationMemoryGovernanceRequest,
    ) -> ConversationMemoryGovernanceRecord:
        targets = self._valid_targets(request, include_inactive=True)
        if not targets:
            return self._record_result(
                request,
                ConversationMemoryOperationStatus.NOT_FOUND,
                reason="physical deletion target became stale or unavailable",
            )
        affected: list[tuple[str, int, int]] = []
        digests: list[str] = []
        target_ids = {item.memory_item_id for item in targets}
        for item in targets:
            digest = hashlib.sha256(item.meaning.encode("utf-8")).hexdigest()
            updated = replace(
                item,
                version=item.version + 1,
                meaning=REDACTED_MEMORY_MEANING,
                updated_at=request.requested_at,
                active=False,
                deactivated_at=request.requested_at,
                deactivation_reason="content was physically deleted by confirmed request",
                physically_deleted=True,
                content_digest=digest,
            )
            self._conversation_items[item.memory_item_id] = updated
            for candidate_id in item.source_candidate_ids:
                candidate = self._conversation_candidates.get(candidate_id)
                if candidate is not None:
                    self._conversation_candidates[candidate_id] = replace(
                        candidate,
                        meaning=REDACTED_MEMORY_MEANING,
                    )
            affected.append((item.memory_item_id, item.version, updated.version))
            digests.append(digest)
        self._governance_records = [
            replace(
                record,
                previous_meaning=REDACTED_MEMORY_MEANING,
                replacement_meaning=REDACTED_MEMORY_MEANING,
            )
            if record.operation is ConversationMemoryOperationKind.CORRECTION
            and record.replaces_item_id in target_ids
            else record
            for record in self._governance_records
        ]
        self._material_version += 1
        return self._record_result(
            request,
            ConversationMemoryOperationStatus.PARTIAL,
            affected=tuple(affected),
            content_digests=tuple(digests),
            residual_scopes=(CHECKPOINT_RESIDUAL_SCOPE,),
        )

    def _valid_targets(
        self,
        request: ConversationMemoryGovernanceRequest,
        *,
        include_inactive: bool = False,
    ) -> list[ConversationMemoryItem]:
        targets: list[ConversationMemoryItem] = []
        for item_id, version in request.target_items:
            item = self._conversation_items.get(item_id)
            if (
                item is None
                or item.physically_deleted
                or (not include_inactive and not item.active)
                or item.counterpart_id != request.counterpart_id
                or item.version != version
                or request.requested_at.value < item.updated_at.value
            ):
                continue
            targets.append(item)
        return targets

    def _record_result(
        self,
        request: ConversationMemoryGovernanceRequest,
        status: ConversationMemoryOperationStatus,
        *,
        reason: str | None = None,
        affected: tuple[tuple[str, int, int], ...] = (),
        previous_meaning: str | None = None,
        replacement_meaning: str | None = None,
        content_digests: tuple[str, ...] = (),
        residual_scopes: tuple[str, ...] = (),
        replaces_item_id: str | None = None,
    ) -> ConversationMemoryGovernanceRecord:
        record = ConversationMemoryGovernanceRecord(
            record_id=self._required_unique_id(
                self._governance_record_id_factory,
                self._governance_record_ids,
                "conversation-memory governance record",
            ),
            request_id=request.request_id,
            lifecycle_id=request.lifecycle_id,
            counterpart_id=request.counterpart_id,
            operation=request.operation,
            status=status,
            affected_items=affected,
            source_reference=request.source_reference,
            reason=reason or request.reason,
            occurred_at=request.requested_at,
            previous_meaning=previous_meaning,
            replacement_meaning=replacement_meaning,
            content_digests=content_digests,
            residual_scopes=residual_scopes,
            replaces_item_id=replaces_item_id,
        )
        self._governance_records.append(record)
        return record
