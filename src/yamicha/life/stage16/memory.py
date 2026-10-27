"""Memory owns observed evidence, confirmed adaptations and their history."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

from yamicha.contracts import (
    DialogueOutput, ExternalTime, MemoryDecisionMaterial, MemoryPersistenceSnapshot,
    OutputReleaseStatus, SensoryEvent,
)
from yamicha.contracts.capability import CapabilityResultEvent
from yamicha.contracts.experience import (
    REACTION_EFFECTS, ExperienceChangeRecord, ExperienceChangeRequest,
    ExperienceChangeStatus, ExperienceEvidence, ExperienceEvidenceKind, ExperienceItem,
    ExperienceOperation, ExperiencePendingConfirmation, ExperienceSelection,
    ExperienceUsage, ExperienceWindow,
)
from yamicha.life.stage14 import Stage14Memory

from .judgment import Stage16Judgment


class Stage16Memory(Stage14Memory):
    def __init__(
        self, *,
        experience_evidence_id_factory: Callable[[], str] | None = None,
        experience_item_id_factory: Callable[[], str] | None = None,
        experience_record_id_factory: Callable[[], str] | None = None,
        experience_confirmation_id_factory: Callable[[], str] | None = None,
        experience_confirmation_ttl_seconds: float = 86400,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        if experience_confirmation_ttl_seconds <= 0:
            raise ValueError("experience confirmation TTL must be positive")
        self._experience_evidence_id_factory = experience_evidence_id_factory or (lambda: str(uuid4()))
        self._experience_item_id_factory = experience_item_id_factory or (lambda: str(uuid4()))
        self._experience_record_id_factory = experience_record_id_factory or (lambda: str(uuid4()))
        self._experience_confirmation_id_factory = experience_confirmation_id_factory or (lambda: str(uuid4()))
        self._experience_confirmation_ttl_seconds = experience_confirmation_ttl_seconds
        self._experience_evidence: dict[str, ExperienceEvidence] = {}
        self._experience_items: dict[str, ExperienceItem] = {}
        self._experience_changes: list[ExperienceChangeRecord] = []
        self._experience_pending: dict[str, ExperiencePendingConfirmation] = {}
        self._experience_usages: dict[str, ExperienceUsage] = {}
        self._experience_evidence_ids: set[str] = set()
        self._experience_item_ids: set[str] = set()
        self._experience_record_ids: set[str] = set()
        self._experience_confirmation_ids: set[str] = set()
        self._experience_request_ids: set[str] = set()
        self._experience_source_references: set[str] = set()
        self._experience_selection: tuple[str, ExperienceWindow] | None = None

    @property
    def experience_evidence(self) -> tuple[ExperienceEvidence, ...]:
        return tuple(self._experience_evidence.values())

    @property
    def experience_items(self) -> tuple[ExperienceItem, ...]:
        return tuple(self._experience_items.values())

    @property
    def experience_changes(self) -> tuple[ExperienceChangeRecord, ...]:
        return tuple(self._experience_changes)

    @property
    def experience_pending_confirmations(self) -> tuple[ExperiencePendingConfirmation, ...]:
        return tuple(self._experience_pending.values())

    @property
    def experience_usages(self) -> tuple[ExperienceUsage, ...]:
        return tuple(self._experience_usages.values())

    def pending_experience_confirmation_for(self, counterpart_id: str) -> ExperiencePendingConfirmation | None:
        return self._experience_pending.get(counterpart_id)

    def list_experiences(self, counterpart_id: str, *, include_inactive: bool = False) -> tuple[ExperienceItem, ...]:
        return tuple(item for item in self._experience_items.values() if item.counterpart_id == counterpart_id and (include_inactive or item.active))

    def record_dialogue_evidence(self, event: SensoryEvent, output: DialogueOutput, context_id: str) -> ExperienceEvidence:
        if output.lifecycle_id != event.correlation_id or output.status is not OutputReleaseStatus.RELEASED or not output.text or Stage16Judgment.is_management_command(event.meaning.normalized_text):
            raise ValueError("experience evidence requires an actually released non-management response")
        return self._register_evidence(
            event, context_id, ExperienceEvidenceKind.DIALOGUE_OUTPUT, output.artifact_id,
            output_digest=hashlib.sha256(output.text.encode("utf-8")).hexdigest(),
        )

    def record_capability_evidence(self, event: CapabilityResultEvent, *, counterpart_id: str, context_id: str) -> ExperienceEvidence:
        result = event.result
        if event.received_at.value < result.completed_at.value:
            raise ValueError("capability observation cannot precede its completion")
        return self._store_evidence(dict(
            kind=ExperienceEvidenceKind.CAPABILITY_RESULT, counterpart_id=counterpart_id,
            context_id=context_id, lifecycle_id=result.request_id, source_reference=result.result_id,
            observed_at=result.completed_at, capability_status=result.status,
            capability_operation=result.operation, capability_target=result.target,
        ))

    def _register_evidence(
        self, event: SensoryEvent, context_id: str, kind: ExperienceEvidenceKind,
        source_reference: str, **values: object,
    ) -> ExperienceEvidence:
        observed_at = values.pop("observed_at", event.received_at)
        fields = dict(kind=kind, counterpart_id=event.source_id, context_id=context_id,
                      lifecycle_id=event.correlation_id, source_reference=source_reference,
                      observed_at=observed_at, **values)
        return self._store_evidence(fields)

    def _store_evidence(self, fields: dict[str, object]) -> ExperienceEvidence:
        previous = next((e for e in self._experience_evidence.values() if e.source_reference == fields["source_reference"]), None)
        if previous is not None:
            if previous != ExperienceEvidence(evidence_id=previous.evidence_id, **fields):
                raise ValueError("observed experience source cannot be rewritten")
            return previous
        evidence = ExperienceEvidence(
            evidence_id=self._required_unique_id(self._experience_evidence_id_factory, self._experience_evidence_ids, "experience evidence"),
            **fields,
        )
        self._experience_evidence[evidence.evidence_id] = evidence
        self._material_version += 1
        return evidence

    def apply_experience_change(self, request: ExperienceChangeRequest) -> ExperienceChangeRecord:
        if request.request_id in self._experience_request_ids or request.source_reference in self._experience_source_references:
            raise ValueError("experience request or source input was already processed")
        if self._experience_changes and request.requested_at.value < self._experience_changes[-1].occurred_at.value:
            raise ValueError("experience update cannot precede its history")
        if request.operation is ExperienceOperation.INVALID:
            return self._record_experience(request, ExperienceChangeStatus.REJECTED, request.reason)
        if request.operation in (ExperienceOperation.CONFIRM, ExperienceOperation.CANCEL):
            return self._resolve_experience_pending(request)
        before = self._experience_items.get(request.target_item_id or "")
        failure = self._validate_experience_proposal(request, before)
        if failure is not None:
            status, reason = failure
            return self._record_experience(request, status, reason)
        pending = self._experience_pending.get(request.counterpart_id)
        if pending is not None and request.requested_at.value >= pending.expires_at.value:
            del self._experience_pending[request.counterpart_id]
            pending = None
        if pending is not None:
            return self._record_experience(request, ExperienceChangeStatus.REJECTED, "既存の経験確認待ちを確認または取り消してください", pending_id=pending.pending_id)
        pending_id = self._required_unique_id(self._experience_confirmation_id_factory, self._experience_confirmation_ids, "experience confirmation")
        self._experience_pending[request.counterpart_id] = ExperiencePendingConfirmation(
            pending_id=pending_id, request=request,
            expires_at=ExternalTime(request.requested_at.value + timedelta(seconds=self._experience_confirmation_ttl_seconds)),
        )
        return self._record_experience(request, ExperienceChangeStatus.CONFIRMATION_REQUIRED,
            "観測への明示反応を受け取りました。変更対象と適用範囲を確認し、別入力で経験確認または経験取消と伝えてください",
            before=before, pending_id=pending_id)

    def _validate_experience_proposal(self, request: ExperienceChangeRequest, before: ExperienceItem | None) -> tuple[ExperienceChangeStatus, str] | None:
        if request.operation not in (ExperienceOperation.ADD, ExperienceOperation.REASSESS, ExperienceOperation.REJECT):
            return ExperienceChangeStatus.REJECTED, "対応する経験操作ではありません"
        if request.operation in (ExperienceOperation.REASSESS, ExperienceOperation.REJECT):
            if before is None:
                return ExperienceChangeStatus.NOT_FOUND, "対象の経験が見つかりません"
            if before.counterpart_id != request.counterpart_id:
                return ExperienceChangeStatus.REJECTED, "経験の変更は同じ相手に限ります"
            if not before.active or before.version != request.expected_version:
                return ExperienceChangeStatus.STALE, "経験の有効状態または版が変わりました"
        evidence = self._experience_evidence.get(request.evidence_id or "")
        if evidence is None or evidence.counterpart_id != request.counterpart_id or (request.operation is not ExperienceOperation.REJECT and evidence.context_id != request.context_id) or evidence.observed_at.value > request.requested_at.value:
            return ExperienceChangeStatus.REJECTED, "この相手と会話に対応する実観測を確認できません"
        if evidence.source_reference == request.source_reference or evidence.lifecycle_id == request.lifecycle_id:
            return ExperienceChangeStatus.REJECTED, "反応は以前の実観測に対して別入力で伝えてください"
        if request.operation is ExperienceOperation.REJECT:
            if before is None or request.evidence_id != before.evidence_id:
                return ExperienceChangeStatus.REJECTED, "不採用対象の根拠が一致しません"
            return None
        target, _ = REACTION_EFFECTS[request.reaction]
        if evidence.kind is ExperienceEvidenceKind.CAPABILITY_RESULT and request.scope_keywords:
            return ExperienceChangeStatus.REJECTED, "能力結果に基づく経験は同じ会話と対象に限定します"
        if request.operation is ExperienceOperation.REASSESS:
            assert before is not None
            if before.context_id != request.context_id and (
                request.scope_keywords != before.scope_keywords
                or not self._has_cross_context_observation(before, evidence)
            ):
                return ExperienceChangeStatus.REJECTED, "別の会話での見直しには、同じ話題範囲で対象の現在版を利用した公開応答が必要です"
            if before.target is not target or evidence.evidence_id == before.evidence_id or evidence.observed_at.value < before.confirmed_at.value:
                return ExperienceChangeStatus.REJECTED, "見直しには採用後の新しい実観測と同じ変更対象が必要です"
            if (before.capability_operation, before.capability_target) != (evidence.capability_operation, evidence.capability_target):
                return ExperienceChangeStatus.REJECTED, "見直しによって能力または対象を変更できません"
        elif any(item.active and item.counterpart_id == request.counterpart_id and item.context_id == request.context_id and item.target is target and item.scope_keywords == request.scope_keywords and item.capability_target == evidence.capability_target for item in self._experience_items.values()):
            return ExperienceChangeStatus.REJECTED, "同じ範囲の経験が有効です。経験見直しで新しい根拠を提示してください"
        return None

    def _has_cross_context_observation(self, before: ExperienceItem, evidence: ExperienceEvidence) -> bool:
        usage = self._experience_usages.get(evidence.lifecycle_id)
        return bool(
            before.scope_keywords
            and before.capability_target is None
            and evidence.kind is ExperienceEvidenceKind.DIALOGUE_OUTPUT
            and usage is not None
            and usage.counterpart_id == before.counterpart_id == evidence.counterpart_id
            and usage.context_id == evidence.context_id
            and usage.occurred_at == evidence.observed_at
            and (before.item_id, before.version) in usage.item_versions
        )

    def _resolve_experience_pending(self, request: ExperienceChangeRequest) -> ExperienceChangeRecord:
        pending = self._experience_pending.get(request.counterpart_id)
        if pending is None or pending.pending_id != request.pending_id:
            return self._record_experience(request, ExperienceChangeStatus.NOT_FOUND, "対応する経験の確認待ちが見つかりません")
        proposed = pending.request
        if request.source_reference == proposed.source_reference or request.lifecycle_id == proposed.lifecycle_id:
            return self._record_experience(request, ExperienceChangeStatus.REJECTED, "別入力で確認または取消してください", pending_id=pending.pending_id)
        if request.requested_at.value < proposed.requested_at.value or request.requested_at.value >= pending.expires_at.value:
            del self._experience_pending[request.counterpart_id]
            return self._record_experience(request, ExperienceChangeStatus.STALE, "経験の確認期限を過ぎました。新しい反応を提示してください", pending_id=pending.pending_id, proposal=proposed)
        if request.operation is ExperienceOperation.CANCEL:
            del self._experience_pending[request.counterpart_id]
            return self._record_experience(request, ExperienceChangeStatus.CANCELLED, "経験の変更を取り消しました", pending_id=pending.pending_id, evidence_id=proposed.evidence_id, proposal=proposed)
        if request.context_id != proposed.context_id:
            return self._record_experience(request, ExperienceChangeStatus.REJECTED, "経験の採用は候補を提示した会話内で確認してください", pending_id=pending.pending_id)
        before = self._experience_items.get(proposed.target_item_id or "")
        failure = self._validate_experience_proposal(proposed, before)
        if failure is not None:
            del self._experience_pending[request.counterpart_id]
            status, reason = failure
            return self._record_experience(request, status, reason, pending_id=pending.pending_id, evidence_id=proposed.evidence_id, proposal=proposed)
        evidence = self._experience_evidence[proposed.evidence_id]
        if proposed.operation is ExperienceOperation.REJECT:
            assert before is not None
            after = replace(before, version=before.version + 1, active=False,
                feedback_source_reference=proposed.source_reference,
                confirmation_reference=request.source_reference, confirmed_at=request.requested_at,
                reason="相手の明示的な不採用申告を別入力で確認した")
        else:
            target, value = REACTION_EFFECTS[proposed.reaction]
            item_id = before.item_id if before is not None else self._required_unique_id(self._experience_item_id_factory, self._experience_item_ids, "experience item")
            after = ExperienceItem(
                item_id=item_id, version=1 if before is None else before.version + 1,
                counterpart_id=request.counterpart_id,
                context_id=proposed.context_id if before is None else before.context_id,
                target=target, value=value, active=True, evidence_id=evidence.evidence_id,
                feedback_source_reference=proposed.source_reference,
                confirmation_reference=request.source_reference, confirmed_at=request.requested_at,
                reason=proposed.reason, scope_keywords=proposed.scope_keywords,
                capability_operation=evidence.capability_operation, capability_target=evidence.capability_target,
            )
        self._experience_items[after.item_id] = after
        del self._experience_pending[request.counterpart_id]
        return self._record_experience(request, ExperienceChangeStatus.COMPLETED,
            "経験の変更を確認し、限定された対話の判断と表現へ反映します" if after.active else "経験を不採用とし、今後の判断材料から除外しました",
            before=before, after=after, pending_id=pending.pending_id, evidence_id=evidence.evidence_id, proposal=proposed)

    def _record_experience(self, request: ExperienceChangeRequest, status: ExperienceChangeStatus, reason: str, *, before: ExperienceItem | None = None, after: ExperienceItem | None = None, pending_id: str | None = None, evidence_id: str | None = None, proposal: ExperienceChangeRequest | None = None) -> ExperienceChangeRecord:
        if proposal is None and request.operation in (ExperienceOperation.ADD, ExperienceOperation.REASSESS, ExperienceOperation.REJECT):
            proposal = request
        affected = after or before
        scope_context = request.context_id if affected is None else affected.context_id
        scope_keywords = request.scope_keywords if affected is None else affected.scope_keywords
        capability_scope = () if affected is None or affected.capability_target is None else (
            f"capability:{affected.capability_operation.value}:{affected.capability_target}",
        )
        record = ExperienceChangeRecord(
            record_id=self._required_unique_id(self._experience_record_id_factory, self._experience_record_ids, "experience record"),
            request_id=request.request_id, lifecycle_id=request.lifecycle_id,
            counterpart_id=request.counterpart_id, operation=request.operation, status=status,
            source_reference=request.source_reference, evidence_id=evidence_id or request.evidence_id,
            before=before, after=after, pending_id=pending_id, reason=reason,
            impact_scope=("dialogue_only", request.counterpart_id, scope_context, *scope_keywords, *capability_scope),
            occurred_at=request.requested_at,
            proposal=proposal,
        )
        self._experience_changes.append(record)
        self._experience_request_ids.add(request.request_id)
        self._experience_source_references.add(request.source_reference)
        self._material_version += 1
        return record

    @staticmethod
    def _experience_applies(item: ExperienceItem, event: SensoryEvent, context_id: str) -> bool:
        if not item.active or item.counterpart_id != event.source_id or item.confirmed_at.value > event.received_at.value:
            return False
        text = event.meaning.normalized_text
        if item.scope_keywords:
            if not any(word in text for word in item.scope_keywords):
                return False
        elif item.context_id != context_id:
            return False
        if item.capability_target is not None:
            target = item.capability_target.replace("\\", "/")
            normalized_text = text.replace("\\", "/")
            # A Japanese postposition may directly follow a quoted path. ASCII
            # path continuations still distinguish document.txt.bak and sibling paths.
            pattern = r"(?<![\w./:-])" + re.escape(target) + r"(?=$|[^\w./:-]|について|を|の|は|が|に|で|と|へ|も|から|まで)"
            if item.context_id != context_id or re.search(pattern, normalized_text) is None:
                return False
        return True

    def select_experience_information(self, event: SensoryEvent, context_id: str, *, max_items: int = 2, max_characters: int = 256) -> ExperienceWindow:
        if max_items < 0 or max_characters < 0:
            raise ValueError("experience selection bounds must not be negative")
        max_items, max_characters = min(max_items, 2), min(max_characters, 256)
        selections: list[ExperienceSelection] = []
        if not Stage16Judgment.is_management_command(event.meaning.normalized_text):
            ordered = sorted(self._experience_items.values(), key=lambda item: (item.confirmed_at.value, item.version, item.item_id), reverse=True)
            for item in ordered:
                if len(selections) >= max_items:
                    break
                if not self._experience_applies(item, event, context_id) or any(s.item.target is item.target for s in selections):
                    continue
                if sum(len(s.item.instruction) for s in selections) + len(item.instruction) > max_characters:
                    continue
                selections.append(ExperienceSelection(item=item, selection_reason="別入力で確認した経験が相手・会話または明示話題・判断種別に一致した"))
        window = ExperienceWindow(current_input_reference=event.event_id, context_id=context_id, memory_version=self._material_version, selections=tuple(selections), max_items=max_items, max_characters=max_characters)
        self._experience_selection = (event.correlation_id, window)
        return window

    def present_decision_material(self, event: SensoryEvent) -> MemoryDecisionMaterial:
        base = super().present_decision_material(event)
        prepared = self._experience_selection
        window = prepared[1] if prepared is not None and prepared[0] == event.correlation_id and prepared[1].current_input_reference == event.event_id else None
        return replace(base, experience_information=window)

    def record_experience_usage(self, event: SensoryEvent, window: ExperienceWindow) -> None:
        if window.current_input_reference != event.event_id:
            raise ValueError("experience usage belongs to another input")
        if not window.selections:
            return
        for selection in window.selections:
            if self._experience_items.get(selection.item.item_id) != selection.item or not self._experience_applies(selection.item, event, window.context_id):
                raise ValueError("experience usage references an obsolete or inapplicable item")
        usage = ExperienceUsage(lifecycle_id=event.correlation_id, source_reference=event.event_id,
            counterpart_id=event.source_id, context_id=window.context_id,
            item_versions=tuple((s.item.item_id, s.item.version) for s in window.selections), occurred_at=event.received_at)
        previous = self._experience_usages.get(event.correlation_id)
        if previous is not None:
            if previous != usage:
                raise ValueError("experience usage cannot be rewritten")
            return
        self._experience_usages[event.correlation_id] = usage
        self._material_version += 1

    def persistence_snapshot(self) -> MemoryPersistenceSnapshot:
        return replace(super().persistence_snapshot(), experience_evidence=self.experience_evidence,
            experience_items=self.experience_items, experience_changes=self.experience_changes,
            experience_pending_confirmations=self.experience_pending_confirmations,
            experience_usages=self.experience_usages)

    def restore_owned_information(self, snapshot: MemoryPersistenceSnapshot) -> None:
        if self._experience_evidence or self._experience_items or self._experience_changes or self._experience_pending or self._experience_usages:
            raise RuntimeError("stage-16 Memory can only restore into a fresh owner")
        super().restore_owned_information(snapshot)
        self._experience_evidence = {e.evidence_id: e for e in snapshot.experience_evidence}
        self._experience_items = {item.item_id: item for item in snapshot.experience_items}
        self._experience_changes = list(snapshot.experience_changes)
        self._experience_pending = {p.request.counterpart_id: p for p in snapshot.experience_pending_confirmations}
        self._experience_usages = {u.lifecycle_id: u for u in snapshot.experience_usages}
        self._experience_evidence_ids = set(self._experience_evidence)
        self._experience_item_ids = set(self._experience_items)
        self._experience_record_ids = {r.record_id for r in self._experience_changes}
        self._experience_confirmation_ids = {r.pending_id for r in self._experience_changes if r.pending_id is not None}
        self._experience_request_ids = {r.request_id for r in self._experience_changes}
        self._experience_source_references = {r.source_reference for r in self._experience_changes}
