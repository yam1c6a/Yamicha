"""Canonical JSON codec for stage-7 persistence snapshots."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from yamicha.contracts import (
    CandidateDisposition,
    CandidateReview,
    CandidateReviewKind,
    ExternalTime,
    InformationCertainty,
    InternalTime,
    LifecycleRecord,
    MemoryItem,
    MemoryPersistenceSnapshot,
    OperatingState,
    PersistenceSnapshot,
    ProtectionPersistenceSnapshot,
    RecordEntry,
    RecordKind,
    RelationshipPersistenceSnapshot,
    ResponsibilityId,
    RetentionCandidate,
    RetentionCandidateKind,
    StatePersistenceSnapshot,
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
        },
        "relationship": {
            "known_counterpart_id": snapshot.relationship.known_counterpart_id,
            "version": snapshot.relationship.version,
        },
        "protection": {
            "normal_dialogue_output_enabled": (
                snapshot.protection.normal_dialogue_output_enabled
            ),
            "version": snapshot.protection.version,
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
        ),
        relationship=RelationshipPersistenceSnapshot(
            known_counterpart_id=str(relationship["known_counterpart_id"]),
            version=int(relationship["version"]),
        ),
        protection=ProtectionPersistenceSnapshot(
            normal_dialogue_output_enabled=bool(
                protection["normal_dialogue_output_enabled"]
            ),
            version=int(protection["version"]),
        ),
    )
