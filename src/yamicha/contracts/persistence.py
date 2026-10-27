"""Stage-7 contracts for persistence identity, checkpoints, and recovery."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .lifecycle import OperatingState
from .retention import CandidateReview, LifecycleRecord, MemoryItem, RecordKind, RetentionCandidate
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
from .experience import (
    REACTION_EFFECTS,
    ExperienceChangeRecord,
    ExperienceChangeStatus,
    ExperienceEvidence,
    ExperienceEvidenceKind,
    ExperienceItem,
    ExperienceOperation,
    ExperiencePendingConfirmation,
    ExperienceUsage,
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
    experience_evidence: tuple[ExperienceEvidence, ...] = ()
    experience_items: tuple[ExperienceItem, ...] = ()
    experience_changes: tuple[ExperienceChangeRecord, ...] = ()
    experience_pending_confirmations: tuple[ExperiencePendingConfirmation, ...] = ()
    experience_usages: tuple[ExperienceUsage, ...] = ()

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
        _validate_experience_information(self)


def _validate_experience_information(snapshot: MemoryPersistenceSnapshot) -> None:
    evidence = {item.evidence_id: item for item in snapshot.experience_evidence}
    items = {item.item_id: item for item in snapshot.experience_items}
    usages = snapshot.experience_usages
    usages_by_lifecycle = {usage.lifecycle_id: usage for usage in usages}
    if len(usages_by_lifecycle) != len(usages):
        raise ValueError("persisted experience usage lifecycle IDs must be unique")
    if (
        len(evidence) != len(snapshot.experience_evidence)
        or len({item.source_reference for item in snapshot.experience_evidence}) != len(evidence)
        or len(items) != len(snapshot.experience_items)
    ):
        raise ValueError("persisted experience evidence and item IDs must be unique")

    def validate_item(item: ExperienceItem) -> None:
        observation = evidence.get(item.evidence_id)
        if observation is None or (
            item.counterpart_id != observation.counterpart_id
            or (
                item.context_id != observation.context_id
                and (not item.scope_keywords or item.capability_target is not None)
            )
            or item.capability_operation != observation.capability_operation
            or item.capability_target != observation.capability_target
            or item.confirmed_at.value < observation.observed_at.value
            or item.feedback_source_reference == observation.source_reference
            or item.confirmation_reference == observation.source_reference
        ):
            raise ValueError("persisted experience item has inconsistent observation provenance")

    def has_cross_context_observation(before: ExperienceItem, observation: ExperienceEvidence) -> bool:
        usage = usages_by_lifecycle.get(observation.lifecycle_id)
        return bool(
            before.scope_keywords
            and before.capability_target is None
            and observation.kind is ExperienceEvidenceKind.DIALOGUE_OUTPUT
            and usage is not None
            and usage.counterpart_id == before.counterpart_id == observation.counterpart_id
            and usage.context_id == observation.context_id
            and usage.occurred_at == observation.observed_at
            and (before.item_id, before.version) in usage.item_versions
        )

    changes = snapshot.experience_changes
    if (
        len({record.record_id for record in changes}) != len(changes)
        or len({record.request_id for record in changes}) != len(changes)
        or len({record.source_reference for record in changes}) != len(changes)
    ):
        raise ValueError("persisted experience change IDs must be unique")
    if any(
        current.occurred_at.value < previous.occurred_at.value
        for previous, current in zip(changes, changes[1:])
    ):
        raise ValueError("persisted experience change history runs backwards in time")
    latest: dict[str, ExperienceItem] = {}
    historical_versions: dict[tuple[str, int], ExperienceItem] = {}
    for record in changes:
        if record.status in (ExperienceChangeStatus.CONFIRMATION_REQUIRED, ExperienceChangeStatus.COMPLETED):
            observation = evidence.get(record.evidence_id)
            if observation is None or observation.counterpart_id != record.counterpart_id:
                raise ValueError("persisted experience change references unknown observation")
        before, after = record.before, record.after
        if before is not None and latest.get(before.item_id) != before:
            raise ValueError("persisted experience history has an inconsistent before state")
        if after is None:
            continue
        validate_item(after)
        if (
            record.status is not ExperienceChangeStatus.COMPLETED
            or record.evidence_id != after.evidence_id
            or record.source_reference != after.confirmation_reference
            or record.occurred_at != after.confirmed_at
        ):
            raise ValueError("persisted experience history has an inconsistent confirmed update")
        proposal = record.proposal
        if proposal is not None:
            if (
                proposal.evidence_id != after.evidence_id
                or proposal.source_reference != after.feedback_source_reference
                or proposal.requested_at.value < evidence[after.evidence_id].observed_at.value
                or (
                    proposal.operation is not ExperienceOperation.REJECT
                    and proposal.context_id != evidence[after.evidence_id].context_id
                )
                or (
                    proposal.operation is ExperienceOperation.ADD
                    and (before is not None or proposal.target_item_id is not None)
                )
                or (
                    proposal.operation in (ExperienceOperation.REASSESS, ExperienceOperation.REJECT)
                    and (
                        before is None
                        or proposal.target_item_id != before.item_id
                        or proposal.expected_version != before.version
                    )
                )
                or (after.active != (proposal.operation is not ExperienceOperation.REJECT))
                or (
                    after.active
                    and (
                        REACTION_EFFECTS.get(proposal.reaction) != (after.target, after.value)
                        or proposal.scope_keywords != after.scope_keywords
                    )
                )
            ):
                raise ValueError("persisted experience update does not match its original proposal")
        if before is None:
            if (after.version != 1 or after.item_id in latest or not after.active
                    or after.context_id != evidence[after.evidence_id].context_id):
                raise ValueError("persisted experience history has an invalid initial version")
        elif (
            before.context_id != after.context_id
            or before.target is not after.target
            or before.confirmed_at.value > after.confirmed_at.value
            or before.capability_operation is not after.capability_operation
            or before.capability_target != after.capability_target
            or (
                after.active
                and (
                    before.evidence_id == after.evidence_id
                    or evidence[after.evidence_id].observed_at.value < before.confirmed_at.value
                    or (
                        before.context_id != evidence[after.evidence_id].context_id
                        and (
                            before.scope_keywords != after.scope_keywords
                            or not has_cross_context_observation(before, evidence[after.evidence_id])
                        )
                    )
                )
            )
            or (
                not after.active
                and (
                    before.evidence_id != after.evidence_id
                    or before.value is not after.value
                    or before.scope_keywords != after.scope_keywords
                )
            )
        ):
            raise ValueError("persisted experience update changes an unsupported scope or lacks new evidence")
        latest[after.item_id] = after
        historical_versions[(after.item_id, after.version)] = after
    if latest != items:
        raise ValueError("persisted experience items do not match their change history")

    pending = snapshot.experience_pending_confirmations
    if (
        len({item.pending_id for item in pending}) != len(pending)
        or len({item.request.request_id for item in pending}) != len(pending)
        or len({item.request.counterpart_id for item in pending}) != len(pending)
    ):
        raise ValueError("persisted experience confirmations must be unique")
    for confirmation in pending:
        request = confirmation.request
        observation = evidence.get(request.evidence_id)
        if observation is None or (
            request.counterpart_id != observation.counterpart_id
            or (
                request.operation is not ExperienceOperation.REJECT
                and request.context_id != observation.context_id
            )
            or request.requested_at.value < observation.observed_at.value
            or request.source_reference == observation.source_reference
            or (observation.capability_target is not None and request.scope_keywords)
        ):
            raise ValueError("persisted experience confirmation has inconsistent observation provenance")
        if request.operation in (ExperienceOperation.REASSESS, ExperienceOperation.REJECT):
            # A pending request can legitimately become stale while a newer item
            # version is retained. Validate its original version, not the latest.
            target = historical_versions.get((request.target_item_id, request.expected_version))
            if target is None or (
                target.counterpart_id != request.counterpart_id
                or (
                    request.operation is not ExperienceOperation.REJECT
                    and target.context_id != request.context_id
                    and (
                        target.scope_keywords != request.scope_keywords
                        or not has_cross_context_observation(target, observation)
                    )
                )
                or not target.active
            ):
                raise ValueError("persisted experience confirmation target is unknown or out of scope")
            if request.operation is ExperienceOperation.REASSESS and (
                request.reaction not in REACTION_EFFECTS
                or REACTION_EFFECTS[request.reaction][0] is not target.target
                or observation.evidence_id == target.evidence_id
                or observation.observed_at.value < target.confirmed_at.value
            ):
                raise ValueError("persisted experience reassessment lacks applicable new evidence")
            if request.operation is ExperienceOperation.REJECT and request.evidence_id != target.evidence_id:
                raise ValueError("persisted experience rejection changes its original observation")

    for usage in usages:
        targets = set()
        for version in usage.item_versions:
            historical = historical_versions.get(version)
            if historical is None or (
                not historical.active
                or historical.counterpart_id != usage.counterpart_id
                or historical.confirmed_at.value > usage.occurred_at.value
                or (not historical.scope_keywords and historical.context_id != usage.context_id)
                or historical.target in targets
            ):
                raise ValueError("persisted experience usage references unknown or inapplicable information")
            targets.add(historical.target)


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
        known_counterpart = self.relationship.known_counterpart_id
        experience_owners = (
            *(item.counterpart_id for item in self.memory.experience_evidence),
            *(item.counterpart_id for item in self.memory.experience_items),
            *(record.counterpart_id for record in self.memory.experience_changes),
            *(pending.request.counterpart_id for pending in self.memory.experience_pending_confirmations),
            *(usage.counterpart_id for usage in self.memory.experience_usages),
        )
        if any(counterpart != known_counterpart for counterpart in experience_owners):
            raise ValueError("persisted experience information crosses known counterparts")
        observed_results = {
            (record.lifecycle_id, entry.source_reference, entry.occurred_at)
            for record in self.lifecycle_records
            for entry in record.entries
            if entry.kind is RecordKind.RESULT
        }
        if any(
            (evidence.lifecycle_id, evidence.source_reference, evidence.observed_at)
            not in observed_results
            for evidence in self.memory.experience_evidence
            if evidence.kind is ExperienceEvidenceKind.DIALOGUE_OUTPUT
        ):
            raise ValueError("persisted dialogue experience has no matching recorded output")


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
