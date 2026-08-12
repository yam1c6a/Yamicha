"""Stage-7 contracts for persistence identity, checkpoints, and recovery."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .lifecycle import OperatingState
from .retention import CandidateReview, LifecycleRecord, MemoryItem, RetentionCandidate
from .time import ExternalTime, InternalTime


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


@dataclass(frozen=True, slots=True)
class RelationshipPersistenceSnapshot:
    known_counterpart_id: str
    version: int = 1

    def __post_init__(self) -> None:
        if not self.known_counterpart_id.strip() or self.version < 1:
            raise ValueError("persisted relationship state is invalid")


@dataclass(frozen=True, slots=True)
class ProtectionPersistenceSnapshot:
    normal_dialogue_output_enabled: bool
    version: int = 1

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("persisted protection state version must be positive")


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
