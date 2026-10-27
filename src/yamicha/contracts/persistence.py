"""Stage-7 contracts for persistence identity, checkpoints, and recovery."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .lifecycle import OperatingState
from .retention import CandidateReview, LifecycleRecord, MemoryItem, RetentionCandidate
from .time import ExternalTime, InternalTime
from .protection import ProtectionMode
from .dialogue import DialogueContext, DialogueContextStatus
from .conversation_memory import (
    ConversationMemoryCandidate,
    ConversationMemoryForgetRecord,
    ConversationMemoryGovernanceRecord,
    ConversationMemoryItem,
    ConversationMemoryPendingConfirmation,
    ConversationMemoryReview,
)
from .relationship import (
    RelationshipChangeRecord,
    RelationshipChangeStatus,
    RelationshipInformationItem,
    RelationshipInformationUsage,
    RelationshipOperation,
    RelationshipPendingConfirmation,
)


class PreviousExit(StrEnum):
    NONE = "none"
    NORMAL = "normal"
    ABNORMAL = "abnormal"


class InitializationKind(StrEnum):
    INITIALIZED = "initialized"
    RESTORED = "restored"


@dataclass(frozen=True, slots=True)
class PersistenceIdentity:
    subject_id: str
    configuration_version: str
    schema_version: int
    created_at: ExternalTime

    def __post_init__(self) -> None:
        if not self.subject_id.strip() or not self.configuration_version.strip():
            raise ValueError("persistence identity values must not be empty")
        if self.schema_version < 1:
            raise ValueError("persistence schema version must be positive")


@dataclass(frozen=True, slots=True)
class StatePersistenceSnapshot:
    operating_state: OperatingState
    internal_time: InternalTime
    last_correlation_id: str
    material_version: int

    def __post_init__(self) -> None:
        if self.operating_state is not OperatingState.WAITING:
            raise ValueError("only a waiting state can be committed for restoration")
        if not self.last_correlation_id.strip():
            raise ValueError("persisted state requires a correlation ID")
        if self.material_version < 1:
            raise ValueError("persisted state material version must be positive")


@dataclass(frozen=True, slots=True)
class MemoryPersistenceSnapshot:
    available: bool
    material_version: int
    candidates: tuple[RetentionCandidate, ...]
    candidate_versions: tuple[tuple[str, int], ...]
    reviews: tuple[CandidateReview, ...]
    items: tuple[MemoryItem, ...]
    conversation_candidates: tuple[ConversationMemoryCandidate, ...] = ()
    conversation_reviews: tuple[ConversationMemoryReview, ...] = ()
    conversation_items: tuple[ConversationMemoryItem, ...] = ()
    conversation_forget_records: tuple[ConversationMemoryForgetRecord, ...] = ()
    conversation_governance_records: tuple[ConversationMemoryGovernanceRecord, ...] = ()
    conversation_pending_confirmations: tuple[ConversationMemoryPendingConfirmation, ...] = ()

    def __post_init__(self) -> None:
        if self.material_version < 1:
            raise ValueError("memory material version must be positive")
        candidate_ids = tuple(candidate.candidate_id for candidate in self.candidates)
        if len(set(candidate_ids)) != len(candidate_ids):
            raise ValueError("persisted candidate IDs must be unique")
        if len({review.review_id for review in self.reviews}) != len(self.reviews):
            raise ValueError("persisted review IDs must be unique")
        if len({item.memory_item_id for item in self.items}) != len(self.items):
            raise ValueError("persisted memory item IDs must be unique")
        versions = dict(self.candidate_versions)
        if len(versions) != len(self.candidate_versions):
            raise ValueError("persisted candidate version IDs must be unique")
        if set(versions) != set(candidate_ids):
            raise ValueError("candidate versions must cover all persisted candidates")
        if any(versions[candidate.candidate_id] != candidate.version for candidate in self.candidates):
            raise ValueError("persisted candidate versions are inconsistent")
        conversation_candidate_ids = tuple(
            candidate.candidate_id for candidate in self.conversation_candidates
        )
        conversation_review_ids = tuple(
            review.review_id for review in self.conversation_reviews
        )
        conversation_item_ids = tuple(
            item.memory_item_id for item in self.conversation_items
        )
        conversation_forget_record_ids = tuple(
            record.record_id for record in self.conversation_forget_records
        )
        conversation_governance_record_ids = tuple(
            record.record_id for record in self.conversation_governance_records
        )
        conversation_confirmation_ids = tuple(
            confirmation.confirmation_id
            for confirmation in self.conversation_pending_confirmations
        )
        conversation_versions = {
            candidate.candidate_id: candidate.version
            for candidate in self.conversation_candidates
        }
        if (
            len(set(conversation_candidate_ids)) != len(conversation_candidate_ids)
            or len(set(conversation_review_ids)) != len(conversation_review_ids)
            or len(set(conversation_item_ids)) != len(conversation_item_ids)
            or len(set(conversation_forget_record_ids))
            != len(conversation_forget_record_ids)
            or len(set(conversation_governance_record_ids))
            != len(conversation_governance_record_ids)
            or len(set(conversation_confirmation_ids))
            != len(conversation_confirmation_ids)
        ):
            raise ValueError("persisted conversation-memory IDs must be unique")
        if any(
            review.candidate_id not in set(conversation_candidate_ids)
            for review in self.conversation_reviews
        ):
            raise ValueError("conversation-memory review references an unknown candidate")
        if any(
            conversation_versions.get(review.candidate_id)
            != review.candidate_version
            for review in self.conversation_reviews
        ):
            raise ValueError("conversation-memory review version is inconsistent")
        if any(
            review.memory_item_id is not None
            and review.memory_item_id not in set(conversation_item_ids)
            for review in self.conversation_reviews
        ):
            raise ValueError("conversation-memory review references an unknown item")
        if any(
            not set(item.source_candidate_ids).issubset(conversation_candidate_ids)
            for item in self.conversation_items
        ):
            raise ValueError("conversation-memory item has unknown candidate provenance")
        if any(
            any(item_id not in set(conversation_item_ids) for item_id, _, _ in record.deactivated_items)
            for record in self.conversation_forget_records
        ):
            raise ValueError("conversation-memory forget record references an unknown item")
        if any(
            any(item_id not in set(conversation_item_ids) for item_id, _, _ in record.affected_items)
            for record in self.conversation_governance_records
        ):
            raise ValueError("conversation-memory governance record references an unknown item")
        if any(
            any(item_id not in set(conversation_item_ids) for item_id, _ in confirmation.request.target_items)
            for confirmation in self.conversation_pending_confirmations
        ):
            raise ValueError("conversation-memory confirmation references an unknown item")


@dataclass(frozen=True, slots=True)
class RelationshipPersistenceSnapshot:
    known_counterpart_id: str
    version: int = 1
    active_dialogue_context: DialogueContext | None = None
    retired_dialogue_context_ids: tuple[str, ...] = ()
    relationship_items: tuple[RelationshipInformationItem, ...] = ()
    relationship_pending_confirmations: tuple[RelationshipPendingConfirmation, ...] = ()
    relationship_changes: tuple[RelationshipChangeRecord, ...] = ()
    relationship_usages: tuple[RelationshipInformationUsage, ...] = ()

    def __post_init__(self) -> None:
        if not self.known_counterpart_id.strip() or self.version < 1:
            raise ValueError("persisted relationship state is invalid")
        if self.active_dialogue_context is not None and (
            self.active_dialogue_context.counterpart_id
            != self.known_counterpart_id
            or self.active_dialogue_context.status
            is not DialogueContextStatus.ACTIVE
        ):
            raise ValueError("persisted active dialogue context is invalid")
        if (
            any(not value.strip() for value in self.retired_dialogue_context_ids)
            or len(set(self.retired_dialogue_context_ids))
            != len(self.retired_dialogue_context_ids)
            or (
                self.active_dialogue_context is not None
                and self.active_dialogue_context.context_id
                in self.retired_dialogue_context_ids
            )
            or (
                self.active_dialogue_context is not None
                and self.active_dialogue_context.previous_context_id is not None
                and self.active_dialogue_context.previous_context_id
                not in self.retired_dialogue_context_ids
            )
        ):
            raise ValueError("persisted retired dialogue context IDs are invalid")
        items = {item.item_id: item for item in self.relationship_items}
        if len(items) != len(self.relationship_items):
            raise ValueError("persisted relationship item IDs must be unique")
        if any(
            item.counterpart_id != self.known_counterpart_id
            for item in self.relationship_items
        ):
            raise ValueError("persisted relationship item crosses counterparts")
        pending_ids = tuple(
            pending.pending_id for pending in self.relationship_pending_confirmations
        )
        pending_requests = tuple(
            pending.request.request_id
            for pending in self.relationship_pending_confirmations
        )
        if (
            len(set(pending_ids)) != len(pending_ids)
            or len(set(pending_requests)) != len(pending_requests)
            or len({
                pending.request.counterpart_id
                for pending in self.relationship_pending_confirmations
            }) != len(self.relationship_pending_confirmations)
        ):
            raise ValueError("persisted relationship confirmations must be unique")
        for pending in self.relationship_pending_confirmations:
            request = pending.request
            if request.counterpart_id != self.known_counterpart_id:
                raise ValueError("persisted relationship confirmation crosses counterparts")
            if request.operation is RelationshipOperation.CORRECT:
                target = items.get(request.target_item_id)
                if target is None:
                    raise ValueError("persisted relationship confirmation target is unknown")
        changes = self.relationship_changes
        if (
            len({record.record_id for record in changes}) != len(changes)
            or len({record.request_id for record in changes}) != len(changes)
        ):
            raise ValueError("persisted relationship change IDs must be unique")
        latest: dict[str, RelationshipInformationItem] = {}
        historical_versions: dict[tuple[str, int], RelationshipInformationItem] = {}
        for record in changes:
            if record.counterpart_id != self.known_counterpart_id:
                raise ValueError("persisted relationship change crosses counterparts")
            if record.before is not None:
                before = record.before
                if latest.get(before.item_id) != before:
                    raise ValueError("persisted relationship history has an inconsistent before state")
            if record.after is None:
                continue
            after = record.after
            if record.status is not RelationshipChangeStatus.COMPLETED:
                raise ValueError("persisted relationship history contains an uncompleted update")
            if record.before is None and (
                after.version != 1 or after.item_id in latest
            ):
                raise ValueError("persisted relationship history has an invalid initial version")
            latest[after.item_id] = after
            historical_versions[(after.item_id, after.version)] = after
        if latest != items:
            raise ValueError("persisted relationship items do not match their change history")
        if len({usage.lifecycle_id for usage in self.relationship_usages}) != len(
            self.relationship_usages
        ):
            raise ValueError("persisted relationship usage lifecycle IDs must be unique")
        if any(
            usage.counterpart_id != self.known_counterpart_id
            or not set(usage.item_versions).issubset(historical_versions)
            or any(
                not historical_versions[version].is_effective_at(usage.occurred_at)
                for version in usage.item_versions
                if version in historical_versions
            )
            for usage in self.relationship_usages
        ):
            raise ValueError("persisted relationship usage references unknown information")


@dataclass(frozen=True, slots=True)
class ProtectionPersistenceSnapshot:
    normal_dialogue_output_enabled: bool
    version: int = 1
    mode: ProtectionMode = ProtectionMode.NORMAL
    definition_version: str = "stage7-protection-v1"
    activation_id: str | None = None

    def __post_init__(self) -> None:
        if self.version < 1 or not self.definition_version.strip():
            raise ValueError("persisted protection state version must be positive")
        if (self.mode is ProtectionMode.PROTECTED) != (
            self.activation_id is not None
        ):
            raise ValueError("protected persistence state requires an activation ID")


@dataclass(frozen=True, slots=True)
class PersistenceSnapshot:
    snapshot_id: str
    sequence: int
    created_at: ExternalTime
    subject_id: str
    configuration_version: str
    state: StatePersistenceSnapshot
    lifecycle_records: tuple[LifecycleRecord, ...]
    memory: MemoryPersistenceSnapshot
    relationship: RelationshipPersistenceSnapshot
    protection: ProtectionPersistenceSnapshot

    def __post_init__(self) -> None:
        required = (
            self.snapshot_id,
            self.subject_id,
            self.configuration_version,
        )
        if not all(value.strip() for value in required) or self.sequence < 1:
            raise ValueError("persistence snapshot identity is invalid")
        record_ids = tuple(record.record_id for record in self.lifecycle_records)
        if len(set(record_ids)) != len(record_ids):
            raise ValueError("persisted lifecycle record IDs must be unique")
        entry_ids = tuple(
            entry.entry_id
            for record in self.lifecycle_records
            for entry in record.entries
        )
        if len(set(entry_ids)) != len(entry_ids):
            raise ValueError("persisted record entry IDs must be unique")


@dataclass(frozen=True, slots=True)
class PersistenceOpenResult:
    identity: PersistenceIdentity
    initialization: InitializationKind
    previous_exit: PreviousExit
    session_id: str
    snapshot: PersistenceSnapshot | None

    def __post_init__(self) -> None:
        if not self.session_id.strip():
            raise ValueError("persistence session ID must not be empty")
        restored = self.initialization is InitializationKind.RESTORED
        if restored != (self.snapshot is not None):
            raise ValueError("only restored initialization carries a snapshot")
