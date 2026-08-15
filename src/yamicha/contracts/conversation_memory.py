"""Contracts for stage-13 conversation-derived long-term memory."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .input import ContentTrust
from .retention import CandidateDisposition, InformationCertainty
from .time import ExternalTime


class ConversationMemoryInterpretationAction(StrEnum):
    REMEMBER = "remember"
    FORGET = "forget"
    NONE = "none"


class ConversationMemorySubject(StrEnum):
    SELF = "self"
    THIRD_PARTY = "third_party"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ConversationMemoryInterpretation:
    interpretation_id: str
    lifecycle_id: str
    action: ConversationMemoryInterpretationAction
    subject: ConversationMemorySubject
    meaning: str | None
    target_text: str | None
    certainty: InformationCertainty
    needs_confirmation: bool
    evidence_quote: str | None
    source_request_id: str
    source_candidate_id: str
    model: str
    interpreted_at: ExternalTime
    untrusted: bool = True

    def __post_init__(self) -> None:
        required = (
            self.interpretation_id,
            self.lifecycle_id,
            self.source_request_id,
            self.source_candidate_id,
            self.model,
        )
        if not all(value.strip() for value in required) or not self.untrusted:
            raise ValueError("conversation-memory interpretation is invalid")
        if self.interpretation_id != self.source_candidate_id:
            raise ValueError("conversation-memory interpretation candidate is inconsistent")
        if self.meaning is not None and not self.meaning.strip():
            raise ValueError("conversation-memory interpretation meaning is blank")
        if self.target_text is not None and not self.target_text.strip():
            raise ValueError("conversation-memory interpretation target is blank")
        if self.evidence_quote is not None and not self.evidence_quote.strip():
            raise ValueError("conversation-memory interpretation evidence is blank")
        if self.action is ConversationMemoryInterpretationAction.REMEMBER:
            if self.meaning is None or self.target_text is not None:
                raise ValueError("remember interpretation requires only meaning")
        elif self.action is ConversationMemoryInterpretationAction.FORGET:
            if self.target_text is None or self.meaning is not None:
                raise ValueError("forget interpretation requires only target text")
        elif self.meaning is not None or self.target_text is not None:
            raise ValueError("none interpretation cannot carry memory content")
        if (
            self.action is not ConversationMemoryInterpretationAction.NONE
            and self.evidence_quote is None
        ):
            raise ValueError("memory action interpretation requires evidence")


@dataclass(frozen=True, slots=True)
class ConversationMemoryCandidate:
    candidate_id: str
    lifecycle_id: str
    counterpart_id: str
    version: int
    meaning: str
    retention_reason: str
    source_turn_ids: tuple[str, ...]
    source_references: tuple[str, ...]
    created_at: ExternalTime
    certainty: InformationCertainty
    content_trust: ContentTrust
    reevaluation_condition: str

    def __post_init__(self) -> None:
        required = (
            self.candidate_id,
            self.lifecycle_id,
            self.counterpart_id,
            self.meaning,
            self.retention_reason,
            self.reevaluation_condition,
        )
        if not all(value.strip() for value in required):
            raise ValueError("conversation-memory candidate is incomplete")
        if self.version < 1 or not self.source_turn_ids or not self.source_references:
            raise ValueError("conversation-memory candidate requires versioned provenance")
        if (
            len(set(self.source_turn_ids)) != len(self.source_turn_ids)
            or len(set(self.source_references)) != len(self.source_references)
        ):
            raise ValueError("conversation-memory candidate provenance must be unique")
        if self.content_trust is not ContentTrust.UNTRUSTED:
            raise ValueError("counterpart memory content must remain untrusted")


@dataclass(frozen=True, slots=True)
class ConversationMemoryReview:
    review_id: str
    candidate_id: str
    candidate_version: int
    disposition: CandidateDisposition
    reason: str
    reviewed_at: ExternalTime
    memory_item_id: str | None = None

    def __post_init__(self) -> None:
        if not self.review_id.strip() or not self.candidate_id.strip() or not self.reason.strip():
            raise ValueError("conversation-memory review is incomplete")
        if self.candidate_version < 1:
            raise ValueError("conversation-memory review version must be positive")
        adopted = self.disposition in {
            CandidateDisposition.ADOPTED,
            CandidateDisposition.INTEGRATED,
        }
        if adopted != (self.memory_item_id is not None):
            raise ValueError("only adopted conversation memory references an item")


@dataclass(frozen=True, slots=True)
class ConversationMemoryItem:
    memory_item_id: str
    counterpart_id: str
    version: int
    meaning: str
    source_candidate_ids: tuple[str, ...]
    source_turn_ids: tuple[str, ...]
    source_references: tuple[str, ...]
    certainty: InformationCertainty
    content_trust: ContentTrust
    created_at: ExternalTime
    updated_at: ExternalTime
    retention_reason: str
    active: bool = True
    deactivated_at: ExternalTime | None = None
    deactivation_reason: str | None = None

    def __post_init__(self) -> None:
        required = (
            self.memory_item_id,
            self.counterpart_id,
            self.meaning,
            self.retention_reason,
        )
        if not all(value.strip() for value in required):
            raise ValueError("conversation-memory item is incomplete")
        if (
            self.version < 1
            or not self.source_candidate_ids
            or not self.source_turn_ids
            or not self.source_references
        ):
            raise ValueError("conversation-memory item requires versioned provenance")
        if self.updated_at.value < self.created_at.value:
            raise ValueError("conversation-memory update cannot precede creation")
        if self.content_trust is not ContentTrust.UNTRUSTED:
            raise ValueError("counterpart memory content must remain untrusted")
        deactivated = self.deactivated_at is not None
        if deactivated != (self.deactivation_reason is not None):
            raise ValueError("conversation-memory deactivation metadata is incomplete")
        if self.active == deactivated:
            raise ValueError("conversation-memory activity and deactivation conflict")
        if self.deactivated_at is not None:
            if self.deactivated_at.value < self.created_at.value:
                raise ValueError("conversation-memory deactivation precedes creation")
            assert self.deactivation_reason is not None
            if not self.deactivation_reason.strip():
                raise ValueError("conversation-memory deactivation reason is empty")


@dataclass(frozen=True, slots=True)
class ConversationMemoryForgetRequest:
    request_id: str
    lifecycle_id: str
    counterpart_id: str
    target_items: tuple[tuple[str, int], ...]
    source_reference: str
    reason: str
    requested_at: ExternalTime

    def __post_init__(self) -> None:
        required = (
            self.request_id,
            self.lifecycle_id,
            self.counterpart_id,
            self.source_reference,
            self.reason,
        )
        if not all(value.strip() for value in required) or not self.target_items:
            raise ValueError("conversation-memory forget request is incomplete")
        item_ids = tuple(item_id for item_id, _ in self.target_items)
        if (
            len(item_ids) != len(set(item_ids))
            or any(not item_id.strip() or version < 1 for item_id, version in self.target_items)
        ):
            raise ValueError("conversation-memory forget targets are invalid")


@dataclass(frozen=True, slots=True)
class ConversationMemoryForgetRecord:
    record_id: str
    request_id: str
    lifecycle_id: str
    counterpart_id: str
    deactivated_items: tuple[tuple[str, int, int], ...]
    source_reference: str
    reason: str
    occurred_at: ExternalTime
    physical_deletion: bool = False

    def __post_init__(self) -> None:
        required = (
            self.record_id,
            self.request_id,
            self.lifecycle_id,
            self.counterpart_id,
            self.source_reference,
            self.reason,
        )
        if not all(value.strip() for value in required) or not self.deactivated_items:
            raise ValueError("conversation-memory forget record is incomplete")
        item_ids = tuple(item_id for item_id, _, _ in self.deactivated_items)
        if (
            len(item_ids) != len(set(item_ids))
            or any(
                not item_id.strip() or old_version < 1 or new_version != old_version + 1
                for item_id, old_version, new_version in self.deactivated_items
            )
        ):
            raise ValueError("conversation-memory deactivation versions are invalid")
        if self.physical_deletion:
            raise ValueError("stage-13 forgetting must not claim physical deletion")


@dataclass(frozen=True, slots=True)
class ConversationMemorySelection:
    memory_item_id: str
    memory_item_version: int
    counterpart_id: str
    meaning: str
    source_references: tuple[str, ...]
    certainty: InformationCertainty
    content_trust: ContentTrust
    selection_reason: str
    updated_at: ExternalTime

    def __post_init__(self) -> None:
        required = (
            self.memory_item_id,
            self.counterpart_id,
            self.meaning,
            self.selection_reason,
        )
        if not all(value.strip() for value in required):
            raise ValueError("conversation-memory selection is incomplete")
        if self.memory_item_version < 1 or not self.source_references:
            raise ValueError("conversation-memory selection requires provenance")
        if self.content_trust is not ContentTrust.UNTRUSTED:
            raise ValueError("selected counterpart memory must remain untrusted")


@dataclass(frozen=True, slots=True)
class ConversationMemoryWindow:
    memory_version: int
    selections: tuple[ConversationMemorySelection, ...]
    current_input_reference: str
    max_items: int
    max_characters: int

    def __post_init__(self) -> None:
        if (
            self.memory_version < 1
            or not self.current_input_reference.strip()
            or self.max_items <= 0
            or self.max_characters < 0
        ):
            raise ValueError("conversation-memory window limits are invalid")
        ids = tuple(item.memory_item_id for item in self.selections)
        if len(ids) != len(set(ids)) or len(ids) > self.max_items:
            raise ValueError("conversation-memory window items are invalid")
        if self.total_characters > self.max_characters:
            raise ValueError("conversation-memory window exceeds its character limit")

    @property
    def total_characters(self) -> int:
        return sum(len(item.meaning) for item in self.selections)
