"""Versioned, explicitly confirmed conversational relationship information.

These values never authorize capabilities, constitute advance approval, or
replace Yamicha's own judgment. Confirmation establishes the counterpart's
stated conversational preference or understanding only.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .retention import InformationCertainty
from .time import ExternalTime


class RelationshipInformationKind(StrEnum):
    PREFERENCE = "preference"
    AGREEMENT = "agreement"
    CONSIDERATION = "consideration"
    ONGOING = "ongoing"
    DISTANCE = "distance"


class RelationshipInformationStatus(StrEnum):
    ACTIVE = "active"
    WITHDRAWN = "withdrawn"


class RelationshipOperation(StrEnum):
    ADD = "add"
    CORRECT = "correct"
    WITHDRAW = "withdraw"
    CONFIRM = "confirm"
    CANCEL = "cancel"
    INVALID = "invalid"


class RelationshipChangeStatus(StrEnum):
    CONFIRMATION_REQUIRED = "confirmation_required"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    NOT_FOUND = "not_found"
    STALE = "stale"


def _validate_scope(scope_keywords: tuple[str, ...]) -> None:
    if (
        len(scope_keywords) > 5
        or len(set(scope_keywords)) != len(scope_keywords)
        or any(not word.strip() or len(word) > 32 for word in scope_keywords)
    ):
        raise ValueError("relationship scope keywords are invalid")


@dataclass(frozen=True, slots=True)
class RelationshipInformationItem:
    item_id: str
    counterpart_id: str
    kind: RelationshipInformationKind
    content: str
    version: int
    status: RelationshipInformationStatus
    source_references: tuple[str, ...]
    provenance: str
    certainty: InformationCertainty
    confirmation_reference: str
    confirmed_at: ExternalTime
    valid_from: ExternalTime
    valid_until: ExternalTime | None
    reconfirmation_condition: str
    scope_keywords: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        required = (
            self.item_id, self.counterpart_id, self.content, self.provenance,
            self.confirmation_reference, self.reconfirmation_condition,
        )
        if not all(value.strip() for value in required) or self.version < 1:
            raise ValueError("relationship information is incomplete")
        if len(self.content) > 256:
            raise ValueError("relationship content exceeds its limit")
        if (
            not self.source_references
            or any(not ref.strip() for ref in self.source_references)
            or len(set(self.source_references)) != len(self.source_references)
            or self.confirmation_reference in self.source_references
        ):
            raise ValueError("relationship information requires separate confirmation")
        if self.certainty is not InformationCertainty.CONFIRMED:
            raise ValueError("active relationship information must be confirmed")
        if self.confirmed_at.value < self.valid_from.value or (
            self.valid_until is not None
            and self.valid_until.value <= self.confirmed_at.value
        ):
            raise ValueError("relationship validity interval is invalid")
        _validate_scope(self.scope_keywords)

    def is_effective_at(self, at: ExternalTime) -> bool:
        return (
            self.status is RelationshipInformationStatus.ACTIVE
            and self.confirmed_at.value <= at.value
            and self.valid_from.value <= at.value
            and (self.valid_until is None or at.value < self.valid_until.value)
        )


@dataclass(frozen=True, slots=True)
class RelationshipChangeRequest:
    request_id: str
    lifecycle_id: str
    counterpart_id: str
    operation: RelationshipOperation
    source_reference: str
    reason: str
    requested_at: ExternalTime
    kind: RelationshipInformationKind | None = None
    content: str | None = None
    target_item_id: str | None = None
    expected_version: int | None = None
    pending_id: str | None = None
    scope_keywords: tuple[str, ...] = ()
    valid_until: ExternalTime | None = None
    reconfirmation_condition: str = "相手の訂正・撤回、期限到達、または適用範囲の変更時に再確認する"

    def __post_init__(self) -> None:
        required = (
            self.request_id, self.lifecycle_id, self.counterpart_id,
            self.source_reference, self.reason, self.reconfirmation_condition,
        )
        if not all(value.strip() for value in required):
            raise ValueError("relationship change request is incomplete")
        if self.content is not None and (
            not self.content.strip() or len(self.content) > 256
        ):
            raise ValueError("relationship request content is invalid")
        if self.operation in (RelationshipOperation.ADD, RelationshipOperation.CORRECT):
            if self.kind is None or self.content is None:
                raise ValueError("relationship addition or correction needs typed content")
        if self.operation in (RelationshipOperation.CORRECT, RelationshipOperation.WITHDRAW):
            if self.target_item_id is None or not self.target_item_id.strip():
                raise ValueError("relationship change requires a target identifier")
        if self.expected_version is not None and self.expected_version < 1:
            raise ValueError("relationship expected version must be positive")
        if self.pending_id is not None and not self.pending_id.strip():
            raise ValueError("relationship pending identifier must not be empty")
        _validate_scope(self.scope_keywords)


@dataclass(frozen=True, slots=True)
class RelationshipPendingConfirmation:
    pending_id: str
    request: RelationshipChangeRequest
    expires_at: ExternalTime

    def __post_init__(self) -> None:
        if (
            not self.pending_id.strip()
            or self.request.operation not in (RelationshipOperation.ADD, RelationshipOperation.CORRECT)
            or self.expires_at.value <= self.request.requested_at.value
        ):
            raise ValueError("relationship pending confirmation is invalid")


@dataclass(frozen=True, slots=True)
class RelationshipChangeRecord:
    record_id: str
    request_id: str
    lifecycle_id: str
    counterpart_id: str
    operation: RelationshipOperation
    status: RelationshipChangeStatus
    source_reference: str
    before: RelationshipInformationItem | None
    after: RelationshipInformationItem | None
    pending_id: str | None
    reason: str
    impact_scope: tuple[str, ...]
    occurred_at: ExternalTime

    def __post_init__(self) -> None:
        required = (
            self.record_id, self.request_id, self.lifecycle_id, self.counterpart_id,
            self.source_reference, self.reason,
        )
        if not all(value.strip() for value in required) or not self.impact_scope:
            raise ValueError("relationship change record is incomplete")
        if any(not value.strip() for value in self.impact_scope):
            raise ValueError("relationship change impact scope must not be empty")
        for item in (self.before, self.after):
            if item is not None and item.counterpart_id != self.counterpart_id:
                raise ValueError("relationship record crosses counterparts")
        if self.before is not None and self.after is not None and (
            self.before.item_id != self.after.item_id
            or self.after.version != self.before.version + 1
        ):
            raise ValueError("relationship record must advance exactly one item version")
        if self.after is not None and self.status is not RelationshipChangeStatus.COMPLETED:
            raise ValueError("only completed relationship changes may have an after state")


@dataclass(frozen=True, slots=True)
class RelationshipInformationSelection:
    item: RelationshipInformationItem
    selection_reason: str

    def __post_init__(self) -> None:
        if self.item.status is not RelationshipInformationStatus.ACTIVE or not self.selection_reason.strip():
            raise ValueError("relationship selection requires active information and a reason")


@dataclass(frozen=True, slots=True)
class RelationshipInformationWindow:
    current_input_reference: str
    relationship_version: int
    selections: tuple[RelationshipInformationSelection, ...]
    max_items: int
    max_characters: int

    def __post_init__(self) -> None:
        if (
            not self.current_input_reference.strip()
            or self.relationship_version < 1
            or self.max_items < 0
            or self.max_characters < 0
            or len(self.selections) > self.max_items
            or self.total_characters > self.max_characters
            or len({selection.item.item_id for selection in self.selections}) != len(self.selections)
        ):
            raise ValueError("relationship information window is invalid")

    @property
    def total_characters(self) -> int:
        return sum(len(selection.item.content) for selection in self.selections)


@dataclass(frozen=True, slots=True)
class RelationshipInformationUsage:
    lifecycle_id: str
    source_reference: str
    counterpart_id: str
    item_versions: tuple[tuple[str, int], ...]
    occurred_at: ExternalTime

    def __post_init__(self) -> None:
        if (
            not all(value.strip() for value in (
                self.lifecycle_id, self.source_reference, self.counterpart_id,
            ))
            or not self.item_versions
            or len({item_id for item_id, _ in self.item_versions}) != len(self.item_versions)
            or any(not item_id.strip() or version < 1 for item_id, version in self.item_versions)
        ):
            raise ValueError("relationship usage is invalid")
