"""Contracts separating stage-6 records, memory, and experience candidates."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .expression import Stage5InputOutcome
from .input import InputDisposition
from .responsibilities import ResponsibilityId
from .time import ExternalTime


class InformationCertainty(StrEnum):
    CONFIRMED = "confirmed"
    INFERRED = "inferred"
    UNKNOWN = "unknown"


class RecordKind(StrEnum):
    EVENT = "event"
    JUDGMENT = "judgment"
    EXPRESSION = "expression"
    RESULT = "result"


@dataclass(frozen=True, slots=True)
class RecordEntry:
    entry_id: str
    lifecycle_id: str
    kind: RecordKind
    source_owner: ResponsibilityId
    source_reference: str
    summary: str
    occurred_at: ExternalTime
    certainty: InformationCertainty
    schema_version: str = "1"

    def __post_init__(self) -> None:
        required = (
            self.entry_id,
            self.lifecycle_id,
            self.source_reference,
            self.summary,
            self.schema_version,
        )
        if not all(value.strip() for value in required):
            raise ValueError("record entry required values must not be empty")


@dataclass(frozen=True, slots=True)
class LifecycleRecord:
    record_id: str
    lifecycle_id: str
    entries: tuple[RecordEntry, ...]
    recorded_at: ExternalTime
    schema_version: str = "1"

    def __post_init__(self) -> None:
        if not self.record_id.strip() or not self.lifecycle_id.strip():
            raise ValueError("lifecycle record identifiers must not be empty")
        if not self.entries:
            raise ValueError("lifecycle record requires at least one entry")
        if any(entry.lifecycle_id != self.lifecycle_id for entry in self.entries):
            raise ValueError("record entries must belong to one lifecycle")
        if len({entry.entry_id for entry in self.entries}) != len(self.entries):
            raise ValueError("record entry IDs must be unique")


class RetentionCandidateKind(StrEnum):
    MEMORY = "memory"
    EXPERIENCE = "experience"


@dataclass(frozen=True, slots=True)
class RetentionCandidate:
    candidate_id: str
    lifecycle_id: str
    kind: RetentionCandidateKind
    proposed_owner: ResponsibilityId
    version: int
    meaning: str
    reason: str
    provenance_entry_ids: tuple[str, ...]
    created_at: ExternalTime
    certainty: InformationCertainty
    reevaluation_condition: str | None
    change_target: str | None = None
    previous_meaning: str | None = None

    def __post_init__(self) -> None:
        required = (
            self.candidate_id,
            self.lifecycle_id,
            self.meaning,
            self.reason,
        )
        if not all(value.strip() for value in required):
            raise ValueError("retention candidate required values must not be empty")
        if self.version < 1 or not self.provenance_entry_ids:
            raise ValueError("candidate requires a version and provenance")
        if (
            self.reevaluation_condition is not None
            and not self.reevaluation_condition.strip()
        ):
            raise ValueError("reevaluation condition must not be blank")
        if (self.change_target is None) != (self.previous_meaning is None):
            raise ValueError(
                "experience change target and previous meaning must appear together"
            )
        if self.change_target is not None and (
            not self.change_target.strip() or not self.previous_meaning.strip()
        ):
            raise ValueError("experience change metadata must not be blank")
        if self.change_target is not None and self.kind is not RetentionCandidateKind.EXPERIENCE:
            raise ValueError("only an experience candidate can describe a change")


class CandidateDisposition(StrEnum):
    ADOPTED = "adopted"
    HELD = "held"
    INTEGRATED = "integrated"
    REJECTED = "rejected"


class CandidateReviewKind(StrEnum):
    INITIAL = "initial"
    REEVALUATION = "reevaluation"


@dataclass(frozen=True, slots=True)
class CandidateReview:
    review_id: str
    candidate_id: str
    candidate_version: int
    owner: ResponsibilityId
    kind: CandidateReviewKind
    disposition: CandidateDisposition
    reason: str
    reviewed_at: ExternalTime
    memory_item_id: str | None = None

    def __post_init__(self) -> None:
        if not self.review_id.strip() or not self.reason.strip():
            raise ValueError("candidate review requires an ID and reason")
        if self.candidate_version < 1:
            raise ValueError("candidate review version must be positive")
        creates_or_updates_item = self.disposition in {
            CandidateDisposition.ADOPTED,
            CandidateDisposition.INTEGRATED,
        }
        if creates_or_updates_item != (self.memory_item_id is not None):
            raise ValueError("only adopted or integrated candidates reference memory")


@dataclass(frozen=True, slots=True)
class MemoryItem:
    memory_item_id: str
    version: int
    source_kind: RetentionCandidateKind
    source_candidate_ids: tuple[str, ...]
    provenance_entry_ids: tuple[str, ...]
    meaning: str
    certainty: InformationCertainty
    created_at: ExternalTime
    updated_at: ExternalTime
    update_reason: str
    active: bool = True

    def __post_init__(self) -> None:
        if not self.memory_item_id.strip() or not self.update_reason.strip():
            raise ValueError("memory item requires an ID and update reason")
        if self.version < 1 or not self.source_candidate_ids:
            raise ValueError("memory item requires versioned candidate provenance")
        if not self.provenance_entry_ids or not self.meaning.strip():
            raise ValueError("memory item requires meaning and record provenance")


@dataclass(frozen=True, slots=True)
class Stage6InputOutcome(Stage5InputOutcome):
    lifecycle_record: LifecycleRecord | None = None
    retention_candidates: tuple[RetentionCandidate, ...] = ()
    candidate_reviews: tuple[CandidateReview, ...] = ()

    def __post_init__(self) -> None:
        Stage5InputOutcome.__post_init__(self)
        should_retain = self.disposition is InputDisposition.ACCEPTED
        has_record = self.lifecycle_record is not None
        if has_record is not should_retain:
            raise ValueError("only newly accepted input creates a lifecycle record")
        if should_retain:
            if len(self.retention_candidates) != 2:
                raise ValueError("stage 6 requires memory and experience candidates")
            if len(self.candidate_reviews) != 2:
                raise ValueError("each retention candidate requires owner review")
        elif self.retention_candidates or self.candidate_reviews:
            raise ValueError("unretained input must not have retention candidates")
