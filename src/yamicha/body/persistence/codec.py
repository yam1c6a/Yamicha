"""Canonical JSON codec for stage-7 persistence snapshots."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from yamicha.contracts import (
    CandidateDisposition,
    CandidateReview,
    CandidateReviewKind,
    ContentTrust,
    ConversationMemoryCandidate,
    ConversationMemoryForgetRecord,
    ConversationMemoryGovernanceRecord,
    ConversationMemoryGovernanceRequest,
    ConversationMemoryItem,
    ConversationMemoryOperationKind,
    ConversationMemoryOperationStatus,
    ConversationMemoryPendingConfirmation,
    ConversationMemoryReview,
    DialogueContext,
    DialogueContextStatus,
    DialogueSpeaker,
    DialogueTurn,
    ExternalTime,
    InformationCertainty,
    InternalTime,
    LifecycleRecord,
    MemoryItem,
    MemoryPersistenceSnapshot,
    OperatingState,
    PersistenceSnapshot,
    ProtectionMode,
    ProtectionPersistenceSnapshot,
    RecordEntry,
    RecordKind,
    RelationshipPersistenceSnapshot,
    ResponsibilityId,
    RetentionCandidate,
    RetentionCandidateKind,
    StatePersistenceSnapshot,
)
from yamicha.contracts.relationship import (
    RelationshipChangeRecord,
    RelationshipChangeRequest,
    RelationshipChangeStatus,
    RelationshipInformationItem,
    RelationshipInformationKind,
    RelationshipInformationStatus,
    RelationshipInformationUsage,
    RelationshipOperation,
    RelationshipPendingConfirmation,
)
from yamicha.contracts.capability import CapabilityOperation, CapabilityResultStatus
from yamicha.contracts.experience import (
    ExperienceChangeRecord,
    ExperienceChangeRequest,
    ExperienceChangeStatus,
    ExperienceEvidence,
    ExperienceEvidenceKind,
    ExperienceItem,
    ExperienceOperation,
    ExperiencePendingConfirmation,
    ExperienceReaction,
    ExperienceTarget,
    ExperienceUsage,
    ExperienceValue,
)


def encode_snapshot(snapshot: PersistenceSnapshot) -> str:
    return json.dumps(
        _snapshot_to_data(snapshot),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def decode_snapshot(payload: str) -> PersistenceSnapshot:
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("snapshot payload must be an object")
    return _snapshot_from_data(data)


def _external(value: ExternalTime) -> str:
    return value.value.isoformat()


def _read_external(value: Any) -> ExternalTime:
    if not isinstance(value, str):
        raise ValueError("external time must be a string")
    return ExternalTime(datetime.fromisoformat(value))


def _entry_to_data(entry: RecordEntry) -> dict[str, Any]:
    return {
        "entry_id": entry.entry_id,
        "lifecycle_id": entry.lifecycle_id,
        "kind": entry.kind.value,
        "source_owner": entry.source_owner.value,
        "source_reference": entry.source_reference,
        "summary": entry.summary,
        "occurred_at": _external(entry.occurred_at),
        "certainty": entry.certainty.value,
        "schema_version": entry.schema_version,
    }


def _read_entry(data: dict[str, Any]) -> RecordEntry:
    return RecordEntry(
        entry_id=str(data["entry_id"]),
        lifecycle_id=str(data["lifecycle_id"]),
        kind=RecordKind(data["kind"]),
        source_owner=ResponsibilityId(data["source_owner"]),
        source_reference=str(data["source_reference"]),
        summary=str(data["summary"]),
        occurred_at=_read_external(data["occurred_at"]),
        certainty=InformationCertainty(data["certainty"]),
        schema_version=str(data["schema_version"]),
    )


def _record_to_data(record: LifecycleRecord) -> dict[str, Any]:
    return {
        "record_id": record.record_id,
        "lifecycle_id": record.lifecycle_id,
        "entries": [_entry_to_data(entry) for entry in record.entries],
        "recorded_at": _external(record.recorded_at),
        "schema_version": record.schema_version,
    }


def _read_record(data: dict[str, Any]) -> LifecycleRecord:
    return LifecycleRecord(
        record_id=str(data["record_id"]),
        lifecycle_id=str(data["lifecycle_id"]),
        entries=tuple(_read_entry(entry) for entry in data["entries"]),
        recorded_at=_read_external(data["recorded_at"]),
        schema_version=str(data["schema_version"]),
    )


def _candidate_to_data(candidate: RetentionCandidate) -> dict[str, Any]:
    return {
        "candidate_id": candidate.candidate_id,
        "lifecycle_id": candidate.lifecycle_id,
        "kind": candidate.kind.value,
        "proposed_owner": candidate.proposed_owner.value,
        "version": candidate.version,
        "meaning": candidate.meaning,
        "reason": candidate.reason,
        "provenance_entry_ids": list(candidate.provenance_entry_ids),
        "created_at": _external(candidate.created_at),
        "certainty": candidate.certainty.value,
        "reevaluation_condition": candidate.reevaluation_condition,
        "change_target": candidate.change_target,
        "previous_meaning": candidate.previous_meaning,
    }


def _read_candidate(data: dict[str, Any]) -> RetentionCandidate:
    return RetentionCandidate(
        candidate_id=str(data["candidate_id"]),
        lifecycle_id=str(data["lifecycle_id"]),
        kind=RetentionCandidateKind(data["kind"]),
        proposed_owner=ResponsibilityId(data["proposed_owner"]),
        version=int(data["version"]),
        meaning=str(data["meaning"]),
        reason=str(data["reason"]),
        provenance_entry_ids=tuple(str(value) for value in data["provenance_entry_ids"]),
        created_at=_read_external(data["created_at"]),
        certainty=InformationCertainty(data["certainty"]),
        reevaluation_condition=(
            None
            if data["reevaluation_condition"] is None
            else str(data["reevaluation_condition"])
        ),
        change_target=(
            None
            if data.get("change_target") is None
            else str(data["change_target"])
        ),
        previous_meaning=(
            None
            if data.get("previous_meaning") is None
            else str(data["previous_meaning"])
        ),
    )


def _review_to_data(review: CandidateReview) -> dict[str, Any]:
    return {
        "review_id": review.review_id,
        "candidate_id": review.candidate_id,
        "candidate_version": review.candidate_version,
        "owner": review.owner.value,
        "kind": review.kind.value,
        "disposition": review.disposition.value,
        "reason": review.reason,
        "reviewed_at": _external(review.reviewed_at),
        "memory_item_id": review.memory_item_id,
    }


def _read_review(data: dict[str, Any]) -> CandidateReview:
    return CandidateReview(
        review_id=str(data["review_id"]),
        candidate_id=str(data["candidate_id"]),
        candidate_version=int(data["candidate_version"]),
        owner=ResponsibilityId(data["owner"]),
        kind=CandidateReviewKind(data["kind"]),
        disposition=CandidateDisposition(data["disposition"]),
        reason=str(data["reason"]),
        reviewed_at=_read_external(data["reviewed_at"]),
        memory_item_id=(
            None if data["memory_item_id"] is None else str(data["memory_item_id"])
        ),
    )


def _item_to_data(item: MemoryItem) -> dict[str, Any]:
    return {
        "memory_item_id": item.memory_item_id,
        "version": item.version,
        "source_kind": item.source_kind.value,
        "source_candidate_ids": list(item.source_candidate_ids),
        "provenance_entry_ids": list(item.provenance_entry_ids),
        "meaning": item.meaning,
        "certainty": item.certainty.value,
        "created_at": _external(item.created_at),
        "updated_at": _external(item.updated_at),
        "update_reason": item.update_reason,
        "active": item.active,
    }


def _read_item(data: dict[str, Any]) -> MemoryItem:
    return MemoryItem(
        memory_item_id=str(data["memory_item_id"]),
        version=int(data["version"]),
        source_kind=RetentionCandidateKind(data["source_kind"]),
        source_candidate_ids=tuple(str(value) for value in data["source_candidate_ids"]),
        provenance_entry_ids=tuple(str(value) for value in data["provenance_entry_ids"]),
        meaning=str(data["meaning"]),
        certainty=InformationCertainty(data["certainty"]),
        created_at=_read_external(data["created_at"]),
        updated_at=_read_external(data["updated_at"]),
        update_reason=str(data["update_reason"]),
        active=bool(data["active"]),
    )


def _conversation_candidate_to_data(
    candidate: ConversationMemoryCandidate,
) -> dict[str, Any]:
    return {
        "candidate_id": candidate.candidate_id,
        "lifecycle_id": candidate.lifecycle_id,
        "counterpart_id": candidate.counterpart_id,
        "version": candidate.version,
        "meaning": candidate.meaning,
        "retention_reason": candidate.retention_reason,
        "source_turn_ids": list(candidate.source_turn_ids),
        "source_references": list(candidate.source_references),
        "created_at": _external(candidate.created_at),
        "certainty": candidate.certainty.value,
        "content_trust": candidate.content_trust.value,
        "reevaluation_condition": candidate.reevaluation_condition,
    }


def _read_conversation_candidate(
    data: dict[str, Any],
) -> ConversationMemoryCandidate:
    return ConversationMemoryCandidate(
        candidate_id=str(data["candidate_id"]),
        lifecycle_id=str(data["lifecycle_id"]),
        counterpart_id=str(data["counterpart_id"]),
        version=int(data["version"]),
        meaning=str(data["meaning"]),
        retention_reason=str(data["retention_reason"]),
        source_turn_ids=tuple(str(value) for value in data["source_turn_ids"]),
        source_references=tuple(str(value) for value in data["source_references"]),
        created_at=_read_external(data["created_at"]),
        certainty=InformationCertainty(data["certainty"]),
        content_trust=ContentTrust(data["content_trust"]),
        reevaluation_condition=str(data["reevaluation_condition"]),
    )


def _conversation_review_to_data(
    review: ConversationMemoryReview,
) -> dict[str, Any]:
    return {
        "review_id": review.review_id,
        "candidate_id": review.candidate_id,
        "candidate_version": review.candidate_version,
        "disposition": review.disposition.value,
        "reason": review.reason,
        "reviewed_at": _external(review.reviewed_at),
        "memory_item_id": review.memory_item_id,
    }


def _read_conversation_review(data: dict[str, Any]) -> ConversationMemoryReview:
    return ConversationMemoryReview(
        review_id=str(data["review_id"]),
        candidate_id=str(data["candidate_id"]),
        candidate_version=int(data["candidate_version"]),
        disposition=CandidateDisposition(data["disposition"]),
        reason=str(data["reason"]),
        reviewed_at=_read_external(data["reviewed_at"]),
        memory_item_id=(
            None
            if data.get("memory_item_id") is None
            else str(data["memory_item_id"])
        ),
    )


def _conversation_item_to_data(item: ConversationMemoryItem) -> dict[str, Any]:
    return {
        "memory_item_id": item.memory_item_id,
        "counterpart_id": item.counterpart_id,
        "version": item.version,
        "meaning": item.meaning,
        "source_candidate_ids": list(item.source_candidate_ids),
        "source_turn_ids": list(item.source_turn_ids),
        "source_references": list(item.source_references),
        "certainty": item.certainty.value,
        "content_trust": item.content_trust.value,
        "created_at": _external(item.created_at),
        "updated_at": _external(item.updated_at),
        "retention_reason": item.retention_reason,
        "active": item.active,
        "deactivated_at": (
            None if item.deactivated_at is None else _external(item.deactivated_at)
        ),
        "deactivation_reason": item.deactivation_reason,
        "physically_deleted": item.physically_deleted,
        "content_digest": item.content_digest,
    }


def _read_conversation_item(data: dict[str, Any]) -> ConversationMemoryItem:
    return ConversationMemoryItem(
        memory_item_id=str(data["memory_item_id"]),
        counterpart_id=str(data["counterpart_id"]),
        version=int(data["version"]),
        meaning=str(data["meaning"]),
        source_candidate_ids=tuple(
            str(value) for value in data["source_candidate_ids"]
        ),
        source_turn_ids=tuple(str(value) for value in data["source_turn_ids"]),
        source_references=tuple(
            str(value) for value in data["source_references"]
        ),
        certainty=InformationCertainty(data["certainty"]),
        content_trust=ContentTrust(data["content_trust"]),
        created_at=_read_external(data["created_at"]),
        updated_at=_read_external(data["updated_at"]),
        retention_reason=str(data["retention_reason"]),
        active=bool(data["active"]),
        deactivated_at=(
            None
            if data.get("deactivated_at") is None
            else _read_external(data["deactivated_at"])
        ),
        deactivation_reason=(
            None
            if data.get("deactivation_reason") is None
            else str(data["deactivation_reason"])
        ),
        physically_deleted=bool(data.get("physically_deleted", False)),
        content_digest=(
            None
            if data.get("content_digest") is None
            else str(data["content_digest"])
        ),
    )


def _conversation_forget_record_to_data(
    record: ConversationMemoryForgetRecord,
) -> dict[str, Any]:
    return {
        "record_id": record.record_id,
        "request_id": record.request_id,
        "lifecycle_id": record.lifecycle_id,
        "counterpart_id": record.counterpart_id,
        "deactivated_items": [list(value) for value in record.deactivated_items],
        "source_reference": record.source_reference,
        "reason": record.reason,
        "occurred_at": _external(record.occurred_at),
        "physical_deletion": record.physical_deletion,
    }


def _read_conversation_forget_record(
    data: dict[str, Any],
) -> ConversationMemoryForgetRecord:
    return ConversationMemoryForgetRecord(
        record_id=str(data["record_id"]),
        request_id=str(data["request_id"]),
        lifecycle_id=str(data["lifecycle_id"]),
        counterpart_id=str(data["counterpart_id"]),
        deactivated_items=tuple(
            (str(value[0]), int(value[1]), int(value[2]))
            for value in data["deactivated_items"]
        ),
        source_reference=str(data["source_reference"]),
        reason=str(data["reason"]),
        occurred_at=_read_external(data["occurred_at"]),
        physical_deletion=bool(data.get("physical_deletion", False)),
    )


def _governance_request_to_data(
    request: ConversationMemoryGovernanceRequest,
) -> dict[str, Any]:
    return {
        "request_id": request.request_id,
        "lifecycle_id": request.lifecycle_id,
        "counterpart_id": request.counterpart_id,
        "operation": request.operation.value,
        "target_items": [list(value) for value in request.target_items],
        "source_reference": request.source_reference,
        "reason": request.reason,
        "requested_at": _external(request.requested_at),
        "replacement_meaning": request.replacement_meaning,
        "replacement_certainty": (
            None
            if request.replacement_certainty is None
            else request.replacement_certainty.value
        ),
        "confirmed": request.confirmed,
    }


def _read_governance_request(
    data: dict[str, Any],
) -> ConversationMemoryGovernanceRequest:
    return ConversationMemoryGovernanceRequest(
        request_id=str(data["request_id"]),
        lifecycle_id=str(data["lifecycle_id"]),
        counterpart_id=str(data["counterpart_id"]),
        operation=ConversationMemoryOperationKind(data["operation"]),
        target_items=tuple(
            (str(value[0]), int(value[1])) for value in data["target_items"]
        ),
        source_reference=str(data["source_reference"]),
        reason=str(data["reason"]),
        requested_at=_read_external(data["requested_at"]),
        replacement_meaning=(
            None
            if data.get("replacement_meaning") is None
            else str(data["replacement_meaning"])
        ),
        replacement_certainty=(
            None
            if data.get("replacement_certainty") is None
            else InformationCertainty(data["replacement_certainty"])
        ),
        confirmed=bool(data.get("confirmed", False)),
    )


def _governance_record_to_data(
    record: ConversationMemoryGovernanceRecord,
) -> dict[str, Any]:
    return {
        "record_id": record.record_id,
        "request_id": record.request_id,
        "lifecycle_id": record.lifecycle_id,
        "counterpart_id": record.counterpart_id,
        "operation": record.operation.value,
        "status": record.status.value,
        "affected_items": [list(value) for value in record.affected_items],
        "source_reference": record.source_reference,
        "reason": record.reason,
        "occurred_at": _external(record.occurred_at),
        "previous_meaning": record.previous_meaning,
        "replacement_meaning": record.replacement_meaning,
        "content_digests": list(record.content_digests),
        "residual_scopes": list(record.residual_scopes),
        "replaces_item_id": record.replaces_item_id,
    }


def _read_governance_record(
    data: dict[str, Any],
) -> ConversationMemoryGovernanceRecord:
    return ConversationMemoryGovernanceRecord(
        record_id=str(data["record_id"]),
        request_id=str(data["request_id"]),
        lifecycle_id=str(data["lifecycle_id"]),
        counterpart_id=str(data["counterpart_id"]),
        operation=ConversationMemoryOperationKind(data["operation"]),
        status=ConversationMemoryOperationStatus(data["status"]),
        affected_items=tuple(
            (str(value[0]), int(value[1]), int(value[2]))
            for value in data["affected_items"]
        ),
        source_reference=str(data["source_reference"]),
        reason=str(data["reason"]),
        occurred_at=_read_external(data["occurred_at"]),
        previous_meaning=(
            None
            if data.get("previous_meaning") is None
            else str(data["previous_meaning"])
        ),
        replacement_meaning=(
            None
            if data.get("replacement_meaning") is None
            else str(data["replacement_meaning"])
        ),
        content_digests=tuple(str(value) for value in data.get("content_digests", ())),
        residual_scopes=tuple(str(value) for value in data.get("residual_scopes", ())),
        replaces_item_id=(
            None
            if data.get("replaces_item_id") is None
            else str(data["replaces_item_id"])
        ),
    )


def _pending_confirmation_to_data(
    confirmation: ConversationMemoryPendingConfirmation,
) -> dict[str, Any]:
    return {
        "confirmation_id": confirmation.confirmation_id,
        "request": _governance_request_to_data(confirmation.request),
        "created_at": _external(confirmation.created_at),
    }


def _read_pending_confirmation(
    data: dict[str, Any],
) -> ConversationMemoryPendingConfirmation:
    return ConversationMemoryPendingConfirmation(
        confirmation_id=str(data["confirmation_id"]),
        request=_read_governance_request(data["request"]),
        created_at=_read_external(data["created_at"]),
    )


def _dialogue_turn_to_data(turn: DialogueTurn) -> dict[str, Any]:
    return {
        "turn_id": turn.turn_id,
        "context_id": turn.context_id,
        "lifecycle_id": turn.lifecycle_id,
        "speaker": turn.speaker.value,
        "text": turn.text,
        "source_reference": turn.source_reference,
        "occurred_at": _external(turn.occurred_at),
        "verified": turn.verified,
        "content_trust": (
            None if turn.content_trust is None else turn.content_trust.value
        ),
    }


def _read_dialogue_turn(data: dict[str, Any]) -> DialogueTurn:
    trust = data.get("content_trust")
    return DialogueTurn(
        turn_id=str(data["turn_id"]),
        context_id=str(data["context_id"]),
        lifecycle_id=str(data["lifecycle_id"]),
        speaker=DialogueSpeaker(data["speaker"]),
        text=str(data["text"]),
        source_reference=str(data["source_reference"]),
        occurred_at=_read_external(data["occurred_at"]),
        verified=bool(data["verified"]),
        content_trust=None if trust is None else ContentTrust(trust),
    )


def _dialogue_context_to_data(context: DialogueContext) -> dict[str, Any]:
    return {
        "context_id": context.context_id,
        "counterpart_id": context.counterpart_id,
        "status": context.status.value,
        "turns": [_dialogue_turn_to_data(turn) for turn in context.turns],
        "version": context.version,
        "started_at": _external(context.started_at),
        "updated_at": _external(context.updated_at),
        "closed_at": (
            None if context.closed_at is None else _external(context.closed_at)
        ),
        "previous_context_id": context.previous_context_id,
    }


def _read_dialogue_context(data: dict[str, Any]) -> DialogueContext:
    closed_at = data.get("closed_at")
    return DialogueContext(
        context_id=str(data["context_id"]),
        counterpart_id=str(data["counterpart_id"]),
        status=DialogueContextStatus(data["status"]),
        turns=tuple(_read_dialogue_turn(turn) for turn in data["turns"]),
        version=int(data["version"]),
        started_at=_read_external(data["started_at"]),
        updated_at=_read_external(data["updated_at"]),
        closed_at=None if closed_at is None else _read_external(closed_at),
        previous_context_id=(
            None
            if data.get("previous_context_id") is None
            else str(data["previous_context_id"])
        ),
    )


def _relationship_item_to_data(item: RelationshipInformationItem) -> dict[str, Any]:
    return {
        "item_id": item.item_id,
        "counterpart_id": item.counterpart_id,
        "kind": item.kind.value,
        "content": item.content,
        "version": item.version,
        "status": item.status.value,
        "source_references": list(item.source_references),
        "provenance": item.provenance,
        "certainty": item.certainty.value,
        "confirmation_reference": item.confirmation_reference,
        "confirmed_at": _external(item.confirmed_at),
        "valid_from": _external(item.valid_from),
        "valid_until": None if item.valid_until is None else _external(item.valid_until),
        "reconfirmation_condition": item.reconfirmation_condition,
        "scope_keywords": list(item.scope_keywords),
    }


def _read_relationship_item(data: dict[str, Any]) -> RelationshipInformationItem:
    valid_until = data["valid_until"]
    return RelationshipInformationItem(
        item_id=str(data["item_id"]),
        counterpart_id=str(data["counterpart_id"]),
        kind=RelationshipInformationKind(data["kind"]),
        content=str(data["content"]),
        version=int(data["version"]),
        status=RelationshipInformationStatus(data["status"]),
        source_references=tuple(str(value) for value in data["source_references"]),
        provenance=str(data["provenance"]),
        certainty=InformationCertainty(data["certainty"]),
        confirmation_reference=str(data["confirmation_reference"]),
        confirmed_at=_read_external(data["confirmed_at"]),
        valid_from=_read_external(data["valid_from"]),
        valid_until=None if valid_until is None else _read_external(valid_until),
        reconfirmation_condition=str(data["reconfirmation_condition"]),
        scope_keywords=tuple(str(value) for value in data["scope_keywords"]),
    )


def _relationship_request_to_data(request: RelationshipChangeRequest) -> dict[str, Any]:
    return {
        "request_id": request.request_id,
        "lifecycle_id": request.lifecycle_id,
        "counterpart_id": request.counterpart_id,
        "operation": request.operation.value,
        "source_reference": request.source_reference,
        "reason": request.reason,
        "requested_at": _external(request.requested_at),
        "kind": None if request.kind is None else request.kind.value,
        "content": request.content,
        "target_item_id": request.target_item_id,
        "expected_version": request.expected_version,
        "pending_id": request.pending_id,
        "scope_keywords": list(request.scope_keywords),
        "valid_until": (
            None if request.valid_until is None else _external(request.valid_until)
        ),
        "reconfirmation_condition": request.reconfirmation_condition,
    }


def _read_relationship_request(data: dict[str, Any]) -> RelationshipChangeRequest:
    valid_until = data["valid_until"]
    kind = data["kind"]
    return RelationshipChangeRequest(
        request_id=str(data["request_id"]),
        lifecycle_id=str(data["lifecycle_id"]),
        counterpart_id=str(data["counterpart_id"]),
        operation=RelationshipOperation(data["operation"]),
        source_reference=str(data["source_reference"]),
        reason=str(data["reason"]),
        requested_at=_read_external(data["requested_at"]),
        kind=None if kind is None else RelationshipInformationKind(kind),
        content=None if data["content"] is None else str(data["content"]),
        target_item_id=(
            None if data["target_item_id"] is None else str(data["target_item_id"])
        ),
        expected_version=(
            None if data["expected_version"] is None else int(data["expected_version"])
        ),
        pending_id=None if data["pending_id"] is None else str(data["pending_id"]),
        scope_keywords=tuple(str(value) for value in data["scope_keywords"]),
        valid_until=None if valid_until is None else _read_external(valid_until),
        reconfirmation_condition=str(data["reconfirmation_condition"]),
    )


def _relationship_pending_to_data(pending: RelationshipPendingConfirmation) -> dict[str, Any]:
    return {
        "pending_id": pending.pending_id,
        "request": _relationship_request_to_data(pending.request),
        "expires_at": _external(pending.expires_at),
    }


def _read_relationship_pending(data: dict[str, Any]) -> RelationshipPendingConfirmation:
    return RelationshipPendingConfirmation(
        pending_id=str(data["pending_id"]),
        request=_read_relationship_request(data["request"]),
        expires_at=_read_external(data["expires_at"]),
    )


def _relationship_change_to_data(record: RelationshipChangeRecord) -> dict[str, Any]:
    return {
        "record_id": record.record_id,
        "request_id": record.request_id,
        "lifecycle_id": record.lifecycle_id,
        "counterpart_id": record.counterpart_id,
        "operation": record.operation.value,
        "status": record.status.value,
        "source_reference": record.source_reference,
        "before": None if record.before is None else _relationship_item_to_data(record.before),
        "after": None if record.after is None else _relationship_item_to_data(record.after),
        "pending_id": record.pending_id,
        "reason": record.reason,
        "impact_scope": list(record.impact_scope),
        "occurred_at": _external(record.occurred_at),
    }


def _read_relationship_change(data: dict[str, Any]) -> RelationshipChangeRecord:
    return RelationshipChangeRecord(
        record_id=str(data["record_id"]),
        request_id=str(data["request_id"]),
        lifecycle_id=str(data["lifecycle_id"]),
        counterpart_id=str(data["counterpart_id"]),
        operation=RelationshipOperation(data["operation"]),
        status=RelationshipChangeStatus(data["status"]),
        source_reference=str(data["source_reference"]),
        before=None if data["before"] is None else _read_relationship_item(data["before"]),
        after=None if data["after"] is None else _read_relationship_item(data["after"]),
        pending_id=None if data["pending_id"] is None else str(data["pending_id"]),
        reason=str(data["reason"]),
        impact_scope=tuple(str(value) for value in data["impact_scope"]),
        occurred_at=_read_external(data["occurred_at"]),
    )


def _relationship_usage_to_data(usage: RelationshipInformationUsage) -> dict[str, Any]:
    return {
        "lifecycle_id": usage.lifecycle_id,
        "source_reference": usage.source_reference,
        "counterpart_id": usage.counterpart_id,
        "item_versions": [list(value) for value in usage.item_versions],
        "occurred_at": _external(usage.occurred_at),
    }


def _read_relationship_usage(data: dict[str, Any]) -> RelationshipInformationUsage:
    return RelationshipInformationUsage(
        lifecycle_id=str(data["lifecycle_id"]),
        source_reference=str(data["source_reference"]),
        counterpart_id=str(data["counterpart_id"]),
        item_versions=tuple((str(value[0]), int(value[1])) for value in data["item_versions"]),
        occurred_at=_read_external(data["occurred_at"]),
    )


def _experience_evidence_to_data(evidence: ExperienceEvidence) -> dict[str, Any]:
    return {
        "evidence_id": evidence.evidence_id,
        "kind": evidence.kind.value,
        "counterpart_id": evidence.counterpart_id,
        "context_id": evidence.context_id,
        "lifecycle_id": evidence.lifecycle_id,
        "source_reference": evidence.source_reference,
        "observed_at": _external(evidence.observed_at),
        "output_digest": evidence.output_digest,
        "capability_status": None if evidence.capability_status is None else evidence.capability_status.value,
        "capability_operation": None if evidence.capability_operation is None else evidence.capability_operation.value,
        "capability_target": evidence.capability_target,
    }


def _read_experience_evidence(data: dict[str, Any]) -> ExperienceEvidence:
    status, operation = data.get("capability_status"), data.get("capability_operation")
    return ExperienceEvidence(
        evidence_id=str(data["evidence_id"]),
        kind=ExperienceEvidenceKind(data["kind"]),
        counterpart_id=str(data["counterpart_id"]),
        context_id=str(data["context_id"]),
        lifecycle_id=str(data["lifecycle_id"]),
        source_reference=str(data["source_reference"]),
        observed_at=_read_external(data["observed_at"]),
        output_digest=None if data.get("output_digest") is None else str(data["output_digest"]),
        capability_status=None if status is None else CapabilityResultStatus(status),
        capability_operation=None if operation is None else CapabilityOperation(operation),
        capability_target=None if data.get("capability_target") is None else str(data["capability_target"]),
    )


def _experience_item_to_data(item: ExperienceItem) -> dict[str, Any]:
    return {
        "item_id": item.item_id,
        "version": item.version,
        "counterpart_id": item.counterpart_id,
        "context_id": item.context_id,
        "target": item.target.value,
        "value": item.value.value,
        "active": item.active,
        "evidence_id": item.evidence_id,
        "feedback_source_reference": item.feedback_source_reference,
        "confirmation_reference": item.confirmation_reference,
        "confirmed_at": _external(item.confirmed_at),
        "reason": item.reason,
        "scope_keywords": list(item.scope_keywords),
        "decision_kind": item.decision_kind,
        "capability_operation": None if item.capability_operation is None else item.capability_operation.value,
        "capability_target": item.capability_target,
        "reevaluation_condition": item.reevaluation_condition,
    }


def _read_experience_item(data: dict[str, Any]) -> ExperienceItem:
    if not isinstance(data["active"], bool):
        raise ValueError("experience active state must be a boolean")
    operation = data.get("capability_operation")
    return ExperienceItem(
        item_id=str(data["item_id"]),
        version=int(data["version"]),
        counterpart_id=str(data["counterpart_id"]),
        context_id=str(data["context_id"]),
        target=ExperienceTarget(data["target"]),
        value=ExperienceValue(data["value"]),
        active=bool(data["active"]),
        evidence_id=str(data["evidence_id"]),
        feedback_source_reference=str(data["feedback_source_reference"]),
        confirmation_reference=str(data["confirmation_reference"]),
        confirmed_at=_read_external(data["confirmed_at"]),
        reason=str(data["reason"]),
        scope_keywords=tuple(str(value) for value in data.get("scope_keywords", ())),
        decision_kind=str(data.get("decision_kind", "dialogue")),
        capability_operation=None if operation is None else CapabilityOperation(operation),
        capability_target=None if data.get("capability_target") is None else str(data["capability_target"]),
        reevaluation_condition=str(data.get("reevaluation_condition", "新しい公開応答への明示反応、訂正、または不採用の申告時に別入力で確認する")),
    )


def _experience_request_to_data(request: ExperienceChangeRequest) -> dict[str, Any]:
    return {
        "request_id": request.request_id,
        "lifecycle_id": request.lifecycle_id,
        "counterpart_id": request.counterpart_id,
        "context_id": request.context_id,
        "operation": request.operation.value,
        "source_reference": request.source_reference,
        "reason": request.reason,
        "requested_at": _external(request.requested_at),
        "reaction": None if request.reaction is None else request.reaction.value,
        "evidence_id": request.evidence_id,
        "target_item_id": request.target_item_id,
        "expected_version": request.expected_version,
        "pending_id": request.pending_id,
        "scope_keywords": list(request.scope_keywords),
    }


def _read_experience_request(data: dict[str, Any]) -> ExperienceChangeRequest:
    reaction = data.get("reaction")
    return ExperienceChangeRequest(
        request_id=str(data["request_id"]),
        lifecycle_id=str(data["lifecycle_id"]),
        counterpart_id=str(data["counterpart_id"]),
        context_id=str(data["context_id"]),
        operation=ExperienceOperation(data["operation"]),
        source_reference=str(data["source_reference"]),
        reason=str(data["reason"]),
        requested_at=_read_external(data["requested_at"]),
        reaction=None if reaction is None else ExperienceReaction(reaction),
        evidence_id=None if data.get("evidence_id") is None else str(data["evidence_id"]),
        target_item_id=None if data.get("target_item_id") is None else str(data["target_item_id"]),
        expected_version=None if data.get("expected_version") is None else int(data["expected_version"]),
        pending_id=None if data.get("pending_id") is None else str(data["pending_id"]),
        scope_keywords=tuple(str(value) for value in data.get("scope_keywords", ())),
    )


def _experience_pending_to_data(pending: ExperiencePendingConfirmation) -> dict[str, Any]:
    return {
        "pending_id": pending.pending_id,
        "request": _experience_request_to_data(pending.request),
        "expires_at": _external(pending.expires_at),
    }


def _read_experience_pending(data: dict[str, Any]) -> ExperiencePendingConfirmation:
    return ExperiencePendingConfirmation(
        pending_id=str(data["pending_id"]),
        request=_read_experience_request(data["request"]),
        expires_at=_read_external(data["expires_at"]),
    )


def _experience_change_to_data(record: ExperienceChangeRecord) -> dict[str, Any]:
    return {
        "record_id": record.record_id,
        "request_id": record.request_id,
        "lifecycle_id": record.lifecycle_id,
        "counterpart_id": record.counterpart_id,
        "operation": record.operation.value,
        "status": record.status.value,
        "source_reference": record.source_reference,
        "evidence_id": record.evidence_id,
        "before": None if record.before is None else _experience_item_to_data(record.before),
        "after": None if record.after is None else _experience_item_to_data(record.after),
        "pending_id": record.pending_id,
        "reason": record.reason,
        "impact_scope": list(record.impact_scope),
        "occurred_at": _external(record.occurred_at),
        "proposal": None if record.proposal is None else _experience_request_to_data(record.proposal),
    }


def _read_experience_change(data: dict[str, Any]) -> ExperienceChangeRecord:
    return ExperienceChangeRecord(
        record_id=str(data["record_id"]),
        request_id=str(data["request_id"]),
        lifecycle_id=str(data["lifecycle_id"]),
        counterpart_id=str(data["counterpart_id"]),
        operation=ExperienceOperation(data["operation"]),
        status=ExperienceChangeStatus(data["status"]),
        source_reference=str(data["source_reference"]),
        evidence_id=None if data.get("evidence_id") is None else str(data["evidence_id"]),
        before=None if data.get("before") is None else _read_experience_item(data["before"]),
        after=None if data.get("after") is None else _read_experience_item(data["after"]),
        pending_id=None if data.get("pending_id") is None else str(data["pending_id"]),
        reason=str(data["reason"]),
        impact_scope=tuple(str(value) for value in data["impact_scope"]),
        occurred_at=_read_external(data["occurred_at"]),
        proposal=None if data.get("proposal") is None else _read_experience_request(data["proposal"]),
    )


def _experience_usage_to_data(usage: ExperienceUsage) -> dict[str, Any]:
    return {
        "lifecycle_id": usage.lifecycle_id,
        "source_reference": usage.source_reference,
        "counterpart_id": usage.counterpart_id,
        "context_id": usage.context_id,
        "item_versions": [list(value) for value in usage.item_versions],
        "occurred_at": _external(usage.occurred_at),
    }


def _read_experience_usage(data: dict[str, Any]) -> ExperienceUsage:
    return ExperienceUsage(
        lifecycle_id=str(data["lifecycle_id"]),
        source_reference=str(data["source_reference"]),
        counterpart_id=str(data["counterpart_id"]),
        context_id=str(data["context_id"]),
        item_versions=tuple((str(value[0]), int(value[1])) for value in data["item_versions"]),
        occurred_at=_read_external(data["occurred_at"]),
    )


def _snapshot_to_data(snapshot: PersistenceSnapshot) -> dict[str, Any]:
    state = snapshot.state
    memory = snapshot.memory
    return {
        "snapshot_id": snapshot.snapshot_id,
        "sequence": snapshot.sequence,
        "created_at": _external(snapshot.created_at),
        "subject_id": snapshot.subject_id,
        "configuration_version": snapshot.configuration_version,
        "state": {
            "operating_state": state.operating_state.value,
            "internal_time": {
                "elapsed_seconds": state.internal_time.elapsed_since_start.total_seconds(),
                "updated_at": _external(state.internal_time.updated_at),
            },
            "last_correlation_id": state.last_correlation_id,
            "material_version": state.material_version,
        },
        "lifecycle_records": [
            _record_to_data(record) for record in snapshot.lifecycle_records
        ],
        "memory": {
            "available": memory.available,
            "material_version": memory.material_version,
            "candidates": [
                _candidate_to_data(candidate) for candidate in memory.candidates
            ],
            "candidate_versions": [list(value) for value in memory.candidate_versions],
            "reviews": [_review_to_data(review) for review in memory.reviews],
            "items": [_item_to_data(item) for item in memory.items],
            "conversation_candidates": [
                _conversation_candidate_to_data(candidate)
                for candidate in memory.conversation_candidates
            ],
            "conversation_reviews": [
                _conversation_review_to_data(review)
                for review in memory.conversation_reviews
            ],
            "conversation_items": [
                _conversation_item_to_data(item)
                for item in memory.conversation_items
            ],
            "conversation_forget_records": [
                _conversation_forget_record_to_data(record)
                for record in memory.conversation_forget_records
            ],
            "conversation_governance_records": [
                _governance_record_to_data(record)
                for record in memory.conversation_governance_records
            ],
            "conversation_pending_confirmations": [
                _pending_confirmation_to_data(confirmation)
                for confirmation in memory.conversation_pending_confirmations
            ],
            "experience_evidence": [
                _experience_evidence_to_data(evidence) for evidence in memory.experience_evidence
            ],
            "experience_items": [
                _experience_item_to_data(item) for item in memory.experience_items
            ],
            "experience_changes": [
                _experience_change_to_data(record) for record in memory.experience_changes
            ],
            "experience_pending_confirmations": [
                _experience_pending_to_data(pending) for pending in memory.experience_pending_confirmations
            ],
            "experience_usages": [
                _experience_usage_to_data(usage) for usage in memory.experience_usages
            ],
        },
        "relationship": {
            "known_counterpart_id": snapshot.relationship.known_counterpart_id,
            "version": snapshot.relationship.version,
            "active_dialogue_context": (
                None
                if snapshot.relationship.active_dialogue_context is None
                else _dialogue_context_to_data(
                    snapshot.relationship.active_dialogue_context
                )
            ),
            "retired_dialogue_context_ids": list(
                snapshot.relationship.retired_dialogue_context_ids
            ),
            "relationship_items": [
                _relationship_item_to_data(item)
                for item in snapshot.relationship.relationship_items
            ],
            "relationship_pending_confirmations": [
                _relationship_pending_to_data(pending)
                for pending in snapshot.relationship.relationship_pending_confirmations
            ],
            "relationship_changes": [
                _relationship_change_to_data(record)
                for record in snapshot.relationship.relationship_changes
            ],
            "relationship_usages": [
                _relationship_usage_to_data(usage)
                for usage in snapshot.relationship.relationship_usages
            ],
        },
        "protection": {
            "normal_dialogue_output_enabled": (
                snapshot.protection.normal_dialogue_output_enabled
            ),
            "version": snapshot.protection.version,
            "mode": snapshot.protection.mode.value,
            "definition_version": snapshot.protection.definition_version,
            "activation_id": snapshot.protection.activation_id,
        },
    }


def _snapshot_from_data(data: dict[str, Any]) -> PersistenceSnapshot:
    state = data["state"]
    internal_time = state["internal_time"]
    memory = data["memory"]
    relationship = data["relationship"]
    protection = data["protection"]
    return PersistenceSnapshot(
        snapshot_id=str(data["snapshot_id"]),
        sequence=int(data["sequence"]),
        created_at=_read_external(data["created_at"]),
        subject_id=str(data["subject_id"]),
        configuration_version=str(data["configuration_version"]),
        state=StatePersistenceSnapshot(
            operating_state=OperatingState(state["operating_state"]),
            internal_time=InternalTime(
                elapsed_since_start=timedelta(
                    seconds=float(internal_time["elapsed_seconds"])
                ),
                updated_at=_read_external(internal_time["updated_at"]),
            ),
            last_correlation_id=str(state["last_correlation_id"]),
            material_version=int(state["material_version"]),
        ),
        lifecycle_records=tuple(
            _read_record(record) for record in data["lifecycle_records"]
        ),
        memory=MemoryPersistenceSnapshot(
            available=bool(memory["available"]),
            material_version=int(memory["material_version"]),
            candidates=tuple(
                _read_candidate(candidate) for candidate in memory["candidates"]
            ),
            candidate_versions=tuple(
                (str(value[0]), int(value[1]))
                for value in memory["candidate_versions"]
            ),
            reviews=tuple(_read_review(review) for review in memory["reviews"]),
            items=tuple(_read_item(item) for item in memory["items"]),
            conversation_candidates=tuple(
                _read_conversation_candidate(candidate)
                for candidate in memory.get("conversation_candidates", ())
            ),
            conversation_reviews=tuple(
                _read_conversation_review(review)
                for review in memory.get("conversation_reviews", ())
            ),
            conversation_items=tuple(
                _read_conversation_item(item)
                for item in memory.get("conversation_items", ())
            ),
            conversation_forget_records=tuple(
                _read_conversation_forget_record(record)
                for record in memory.get("conversation_forget_records", ())
            ),
            conversation_governance_records=tuple(
                _read_governance_record(record)
                for record in memory.get("conversation_governance_records", ())
            ),
            conversation_pending_confirmations=tuple(
                _read_pending_confirmation(confirmation)
                for confirmation in memory.get(
                    "conversation_pending_confirmations", ()
                )
            ),
            experience_evidence=tuple(
                _read_experience_evidence(evidence)
                for evidence in memory.get("experience_evidence", ())
            ),
            experience_items=tuple(
                _read_experience_item(item)
                for item in memory.get("experience_items", ())
            ),
            experience_changes=tuple(
                _read_experience_change(record)
                for record in memory.get("experience_changes", ())
            ),
            experience_pending_confirmations=tuple(
                _read_experience_pending(pending)
                for pending in memory.get("experience_pending_confirmations", ())
            ),
            experience_usages=tuple(
                _read_experience_usage(usage)
                for usage in memory.get("experience_usages", ())
            ),
        ),
        relationship=RelationshipPersistenceSnapshot(
            known_counterpart_id=str(relationship["known_counterpart_id"]),
            version=int(relationship["version"]),
            active_dialogue_context=(
                None
                if relationship.get("active_dialogue_context") is None
                else _read_dialogue_context(relationship["active_dialogue_context"])
            ),
            retired_dialogue_context_ids=tuple(
                str(value)
                for value in relationship.get("retired_dialogue_context_ids", ())
            ),
            relationship_items=tuple(
                _read_relationship_item(item)
                for item in relationship.get("relationship_items", ())
            ),
            relationship_pending_confirmations=tuple(
                _read_relationship_pending(pending)
                for pending in relationship.get("relationship_pending_confirmations", ())
            ),
            relationship_changes=tuple(
                _read_relationship_change(record)
                for record in relationship.get("relationship_changes", ())
            ),
            relationship_usages=tuple(
                _read_relationship_usage(usage)
                for usage in relationship.get("relationship_usages", ())
            ),
        ),
        protection=ProtectionPersistenceSnapshot(
            normal_dialogue_output_enabled=bool(
                protection["normal_dialogue_output_enabled"]
            ),
            version=int(protection["version"]),
            mode=ProtectionMode(protection.get("mode", "normal")),
            definition_version=str(
                protection.get("definition_version", "stage7-protection-v1")
            ),
            activation_id=(
                None
                if protection.get("activation_id") is None
                else str(protection["activation_id"])
            ),
        ),
    )
