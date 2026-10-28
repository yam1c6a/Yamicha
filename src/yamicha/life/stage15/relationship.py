"""Relationship-owned information, explicit confirmation, history, and selection."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

from yamicha.contracts import (
    DialogueContextWindow,
    DialogueSpeaker,
    DialogueTurn,
    ExternalTime,
    InformationCertainty,
    RelationshipDecisionMaterial,
    RelationshipPersistenceSnapshot,
    SensoryEvent,
)
from yamicha.contracts.relationship import (
    RelationshipChangeRecord,
    RelationshipChangeRequest,
    RelationshipChangeStatus,
    RelationshipInformationItem,
    RelationshipInformationSelection,
    RelationshipInformationStatus,
    RelationshipInformationUsage,
    RelationshipInformationWindow,
    RelationshipOperation,
    RelationshipPendingConfirmation,
)
from yamicha.life.stage14 import Stage14Relationship

from .judgment import Stage15Judgment


class Stage15Relationship(Stage14Relationship):
    def __init__(
        self,
        *,
        relationship_item_id_factory: Callable[[], str] | None = None,
        relationship_record_id_factory: Callable[[], str] | None = None,
        relationship_confirmation_id_factory: Callable[[], str] | None = None,
        relationship_confirmation_ttl_seconds: float = 86400,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        if relationship_confirmation_ttl_seconds <= 0:
            raise ValueError("relationship confirmation TTL must be positive")
        self._relationship_item_id_factory = relationship_item_id_factory or (lambda: str(uuid4()))
        self._relationship_record_id_factory = relationship_record_id_factory or (lambda: str(uuid4()))
        self._relationship_confirmation_id_factory = relationship_confirmation_id_factory or (lambda: str(uuid4()))
        self._relationship_confirmation_ttl_seconds = relationship_confirmation_ttl_seconds
        self._relationship_items: dict[str, RelationshipInformationItem] = {}
        self._relationship_changes: list[RelationshipChangeRecord] = []
        self._relationship_pending: dict[str, RelationshipPendingConfirmation] = {}
        self._relationship_usages: dict[str, RelationshipInformationUsage] = {}
        self._relationship_item_ids: set[str] = set()
        self._relationship_record_ids: set[str] = set()
        self._relationship_confirmation_ids: set[str] = set()
        self._relationship_request_ids: set[str] = set()
        self._relationship_source_references: set[str] = set()

    @property
    def relationship_items(self) -> tuple[RelationshipInformationItem, ...]:
        return tuple(self._relationship_items.values())

    @property
    def relationship_changes(self) -> tuple[RelationshipChangeRecord, ...]:
        return tuple(self._relationship_changes)

    @property
    def relationship_pending_confirmations(self) -> tuple[RelationshipPendingConfirmation, ...]:
        return tuple(self._relationship_pending.values())

    @property
    def relationship_usages(self) -> tuple[RelationshipInformationUsage, ...]:
        return tuple(self._relationship_usages.values())

    def pending_relationship_confirmation_for(self, counterpart_id: str) -> RelationshipPendingConfirmation | None:
        return self._relationship_pending.get(counterpart_id)

    def pending_confirmation_for(self, counterpart_id: str) -> RelationshipPendingConfirmation | None:
        return self.pending_relationship_confirmation_for(counterpart_id)

    def list_relationship_information(
        self,
        counterpart_id: str,
        *,
        include_inactive: bool = False,
    ) -> tuple[RelationshipInformationItem, ...]:
        if counterpart_id != self._known_counterpart_id:
            return ()
        return tuple(
            item for item in self._relationship_items.values()
            if include_inactive or item.status is RelationshipInformationStatus.ACTIVE
        )

    def apply_relationship_change(self, request: RelationshipChangeRequest) -> RelationshipChangeRecord:
        if request.counterpart_id != self._known_counterpart_id:
            raise ValueError("relationship update belongs to another counterpart")
        if request.request_id in self._relationship_request_ids or request.source_reference in self._relationship_source_references:
            raise ValueError("relationship change was already processed")
        if self._relationship_changes and request.requested_at.value < self._relationship_changes[-1].occurred_at.value:
            raise ValueError("relationship update cannot precede its history")
        if request.operation is RelationshipOperation.INVALID:
            return self._record(request, RelationshipChangeStatus.REJECTED, request.reason)
        if request.operation in (RelationshipOperation.CONFIRM, RelationshipOperation.CANCEL):
            return self._resolve_pending(request)
        before = self._relationship_items.get(request.target_item_id or "")
        if request.operation in (RelationshipOperation.CORRECT, RelationshipOperation.WITHDRAW):
            if before is None:
                return self._record(request, RelationshipChangeStatus.NOT_FOUND, "指定された関係情報が見つかりません")
            if before.status is not RelationshipInformationStatus.ACTIVE or before.version != request.expected_version:
                return self._record(request, RelationshipChangeStatus.STALE, "対象の版または有効状態が変わっています", before=before)
        if request.operation is RelationshipOperation.WITHDRAW:
            assert before is not None
            after = replace(before, version=before.version + 1, status=RelationshipInformationStatus.WITHDRAWN)
            self._relationship_items[before.item_id] = after
            return self._record(request, RelationshipChangeStatus.COMPLETED, "指定された関係情報を撤回し、以後の判断材料から除外しました", before=before, after=after)
        if request.operation not in (RelationshipOperation.ADD, RelationshipOperation.CORRECT):
            return self._record(request, RelationshipChangeStatus.REJECTED, "この関係操作には対応していません")
        pending = self._relationship_pending.get(request.counterpart_id)
        if pending is not None:
            return self._record(request, RelationshipChangeStatus.REJECTED, "既存の確認待ちを確認または取り消してから変更してください", before=before, pending_id=pending.pending_id)
        if request.valid_until is not None and request.valid_until.value <= request.requested_at.value:
            return self._record(request, RelationshipChangeStatus.REJECTED, "有効期限を過ぎた関係情報は追加・訂正できません", before=before)
        if request.operation is RelationshipOperation.ADD and any(
            item.is_effective_at(request.requested_at)
            and item.kind is request.kind and item.content == request.content
            and item.scope_keywords == request.scope_keywords
            for item in self._relationship_items.values()
        ):
            return self._record(request, RelationshipChangeStatus.REJECTED, "同じ関係情報がすでに有効です")
        pending_id = self._required_unique_id(self._relationship_confirmation_id_factory, self._relationship_confirmation_ids, "relationship confirmation")
        expires = request.requested_at.value + timedelta(seconds=self._relationship_confirmation_ttl_seconds)
        if request.valid_until is not None:
            expires = min(expires, request.valid_until.value)
        self._relationship_pending[request.counterpart_id] = RelationshipPendingConfirmation(
            pending_id=pending_id, request=request, expires_at=ExternalTime(expires),
        )
        return self._record(request, RelationshipChangeStatus.CONFIRMATION_REQUIRED, "会話上の関係情報として扱います。内容と適用範囲を確認し、関係確認または関係取消で回答してください", before=before, pending_id=pending_id)

    def _resolve_pending(self, request: RelationshipChangeRequest) -> RelationshipChangeRecord:
        pending = self._relationship_pending.get(request.counterpart_id)
        if pending is None or request.pending_id != pending.pending_id:
            return self._record(request, RelationshipChangeStatus.NOT_FOUND, "対応する関係情報の確認待ちが見つかりません")
        proposed = pending.request
        if request.source_reference == proposed.source_reference or request.lifecycle_id == proposed.lifecycle_id:
            return self._record(request, RelationshipChangeStatus.REJECTED, "関係情報は別の入力で確認してください", pending_id=pending.pending_id)
        if request.operation is RelationshipOperation.CANCEL:
            del self._relationship_pending[request.counterpart_id]
            return self._record(request, RelationshipChangeStatus.CANCELLED, "関係情報の変更を取り消しました", pending_id=pending.pending_id)
        if request.requested_at.value < proposed.requested_at.value or request.requested_at.value >= pending.expires_at.value:
            del self._relationship_pending[request.counterpart_id]
            return self._record(request, RelationshipChangeStatus.STALE, "確認期限を過ぎています。内容を改めて提示してください", pending_id=pending.pending_id)
        before = self._relationship_items.get(proposed.target_item_id or "")
        if proposed.operation is RelationshipOperation.CORRECT and (
            before is None or before.version != proposed.expected_version
            or before.status is not RelationshipInformationStatus.ACTIVE
        ):
            del self._relationship_pending[request.counterpart_id]
            return self._record(request, RelationshipChangeStatus.STALE, "確認待ちの間に対象の版または有効状態が変わりました", before=before, pending_id=pending.pending_id)
        assert proposed.kind is not None and proposed.content is not None
        item_id = before.item_id if before is not None else self._required_unique_id(
            self._relationship_item_id_factory, self._relationship_item_ids, "relationship information",
        )
        after = RelationshipInformationItem(
            item_id=item_id,
            counterpart_id=request.counterpart_id,
            kind=proposed.kind,
            content=proposed.content,
            version=1 if before is None else before.version + 1,
            status=RelationshipInformationStatus.ACTIVE,
            source_references=tuple(dict.fromkeys((*(() if before is None else before.source_references), proposed.source_reference))),
            provenance="counterpart_confirmed_conversational_information",
            certainty=InformationCertainty.CONFIRMED,
            confirmation_reference=request.source_reference,
            confirmed_at=request.requested_at,
            valid_from=proposed.requested_at,
            valid_until=proposed.valid_until,
            reconfirmation_condition=proposed.reconfirmation_condition,
            scope_keywords=proposed.scope_keywords,
        )
        self._relationship_items[item_id] = after
        del self._relationship_pending[request.counterpart_id]
        return self._record(request, RelationshipChangeStatus.COMPLETED, "確認された関係情報を保存しました。適用範囲内の会話判断へ提示します", before=before, after=after, pending_id=pending.pending_id)

    def _record(
        self,
        request: RelationshipChangeRequest,
        status: RelationshipChangeStatus,
        reason: str,
        *,
        before: RelationshipInformationItem | None = None,
        after: RelationshipInformationItem | None = None,
        pending_id: str | None = None,
    ) -> RelationshipChangeRecord:
        record_id = self._required_unique_id(self._relationship_record_id_factory, self._relationship_record_ids, "relationship change")
        scope = after.scope_keywords if after is not None else (before.scope_keywords if before is not None else request.scope_keywords)
        record = RelationshipChangeRecord(
            record_id=record_id, request_id=request.request_id,
            lifecycle_id=request.lifecycle_id, counterpart_id=request.counterpart_id,
            operation=request.operation, status=status, source_reference=request.source_reference,
            before=before, after=after, pending_id=pending_id, reason=reason,
            impact_scope=("conversation_only", *(scope or ("all_dialogue",))),
            occurred_at=request.requested_at,
        )
        self._relationship_changes.append(record)
        self._relationship_request_ids.add(request.request_id)
        self._relationship_source_references.add(request.source_reference)
        self._relationship_version += 1
        return record

    def select_relationship_information(
        self,
        event: SensoryEvent,
        *,
        max_items: int = 5,
        max_characters: int = 512,
    ) -> RelationshipInformationWindow:
        if max_items < 0 or max_characters < 0:
            raise ValueError("relationship selection limits must not be negative")
        max_items, max_characters = min(max_items, 5), min(max_characters, 512)
        selections: list[RelationshipInformationSelection] = []
        remaining = max_characters
        if event.source_id == self._known_counterpart_id and not Stage15Judgment.is_relationship_command(event.meaning.normalized_text):
            for item in reversed(tuple(self._relationship_items.values())):
                if not item.is_effective_at(event.received_at):
                    continue
                if item.scope_keywords and not any(word in event.meaning.normalized_text for word in item.scope_keywords):
                    continue
                if len(selections) >= max_items:
                    break
                if len(item.content) > remaining:
                    continue
                selections.append(RelationshipInformationSelection(
                    item=item,
                    selection_reason="confirmed relationship information applies to this conversation" if not item.scope_keywords else "confirmed relationship information matches the current topic",
                ))
                remaining -= len(item.content)
        return RelationshipInformationWindow(
            current_input_reference=event.event_id,
            relationship_version=self._relationship_version,
            selections=tuple(selections), max_items=max_items, max_characters=max_characters,
        )

    def present_decision_material(self, event: SensoryEvent) -> RelationshipDecisionMaterial:
        base = super().present_decision_material(event)
        return replace(base, relationship_information=self.select_relationship_information(event))

    def record_relationship_usage(self, event: SensoryEvent, window: RelationshipInformationWindow) -> None:
        if event.source_id != self._known_counterpart_id or window.current_input_reference != event.event_id:
            raise ValueError("relationship usage belongs to another input")
        if not window.selections:
            return
        for selection in window.selections:
            item = self._relationship_items.get(selection.item.item_id)
            if item != selection.item or not item.is_effective_at(event.received_at):
                raise ValueError("relationship usage references an obsolete item")
        usage = RelationshipInformationUsage(
            lifecycle_id=event.correlation_id, source_reference=event.event_id,
            counterpart_id=event.source_id,
            item_versions=tuple((selection.item.item_id, selection.item.version) for selection in window.selections),
            occurred_at=event.received_at,
        )
        previous = self._relationship_usages.get(event.correlation_id)
        if previous is not None:
            if previous != usage:
                raise ValueError("relationship usage cannot change after it was recorded")
            return
        self._relationship_usages[event.correlation_id] = usage
        self._relationship_version += 1

    def select_dialogue_window(
        self,
        event: SensoryEvent,
        *,
        max_exchanges: int,
        max_characters: int,
        relationship_information: RelationshipInformationWindow | None = None,
        excluded_lifecycle_ids: tuple[str, ...] = (),
        required_exchange: tuple[DialogueTurn, ...] = (),
    ) -> DialogueContextWindow:
        context = self.ensure_active_context(event)
        current_characters = len(event.meaning.normalized_text)
        if current_characters > max_characters:
            raise ValueError("current input exceeds the dialogue context limit")
        relationship_window = (
            self.select_relationship_information(event)
            if relationship_information is None else relationship_information
        )
        if relationship_window.current_input_reference != event.event_id:
            raise ValueError("relationship dialogue scope belongs to another input")
        excluded = self.dialogue_exclusions(event, relationship_window)
        excluded.update(excluded_lifecycle_ids)
        if required_exchange:
            if (len(required_exchange) != 2 or context.turns[-2:] != required_exchange
                    or required_exchange[0].lifecycle_id != required_exchange[1].lifecycle_id
                    or current_characters + sum(len(turn.text) for turn in required_exchange) > max_characters):
                raise ValueError("required exchange must be the current bounded dialogue tail")
            excluded.discard(required_exchange[0].lifecycle_id)
        exchanges: list[list[DialogueTurn]] = []
        for turn in context.turns:
            if turn.lifecycle_id in excluded:
                continue
            if not exchanges or exchanges[-1][0].lifecycle_id != turn.lifecycle_id:
                exchanges.append([turn])
            else:
                exchanges[-1].append(turn)
        remaining = max_characters - current_characters
        selected: list[list[DialogueTurn]] = []
        for exchange in reversed(exchanges[-max_exchanges:]):
            characters = sum(len(turn.text) for turn in exchange)
            if characters > remaining:
                break
            selected.append(exchange)
            remaining -= characters
        return DialogueContextWindow(
            context_id=context.context_id, context_version=context.version,
            turns=tuple(turn for exchange in reversed(selected) for turn in exchange),
            current_input_reference=event.event_id,
            current_input_characters=current_characters,
            max_exchanges=max_exchanges, max_characters=max_characters,
        )

    def dialogue_exclusions(
        self, event: SensoryEvent, relationship_window: RelationshipInformationWindow,
        *, exclude_unselected: bool = True,
    ) -> set[str]:
        context = self.ensure_active_context(event)
        selected_relationship_versions = {
            (selection.item.item_id, selection.item.version)
            for selection in relationship_window.selections
        }
        excluded = {record.lifecycle_id for record in self._relationship_changes}
        excluded.update(
            turn.lifecycle_id for turn in context.turns
            if turn.speaker is DialogueSpeaker.HUMAN
            and Stage15Judgment.is_relationship_command(turn.text)
        )
        for usage in self._relationship_usages.values():
            if any(
                (exclude_unselected and (item_id, version) not in selected_relationship_versions)
                or item_id not in self._relationship_items
                or self._relationship_items[item_id].version != version
                or not self._relationship_items[item_id].is_effective_at(event.received_at)
                or (
                    exclude_unselected and self._relationship_items[item_id].scope_keywords
                    and not any(word in event.meaning.normalized_text for word in self._relationship_items[item_id].scope_keywords)
                )
                for item_id, version in usage.item_versions
            ):
                excluded.add(usage.lifecycle_id)
        return excluded

    def persistence_snapshot(self) -> RelationshipPersistenceSnapshot:
        return replace(
            super().persistence_snapshot(),
            relationship_items=self.relationship_items,
            relationship_changes=self.relationship_changes,
            relationship_pending_confirmations=self.relationship_pending_confirmations,
            relationship_usages=self.relationship_usages,
        )

    def restore_owned_state(self, snapshot: RelationshipPersistenceSnapshot) -> None:
        if self._relationship_items or self._relationship_changes or self._relationship_pending or self._relationship_usages:
            raise RuntimeError("Relationship can only restore into a fresh owner")
        super().restore_owned_state(snapshot)
        self._relationship_items = {item.item_id: item for item in snapshot.relationship_items}
        self._relationship_changes = list(snapshot.relationship_changes)
        self._relationship_pending = {pending.request.counterpart_id: pending for pending in snapshot.relationship_pending_confirmations}
        self._relationship_usages = {usage.lifecycle_id: usage for usage in snapshot.relationship_usages}
        self._relationship_item_ids.update(self._relationship_items)
        self._relationship_record_ids.update(record.record_id for record in self._relationship_changes)
        self._relationship_request_ids.update(record.request_id for record in self._relationship_changes)
        self._relationship_source_references.update(record.source_reference for record in self._relationship_changes)
        self._relationship_confirmation_ids.update(record.pending_id for record in self._relationship_changes if record.pending_id is not None)
