"""Bounded experience grounded in observed output and separate human confirmation.

Observation establishes only that an output was released or a capability returned
its reported result. A person's reaction never changes that observed result.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .capability import CapabilityOperation, CapabilityResultStatus
from .time import ExternalTime


class ExperienceEvidenceKind(StrEnum):
    DIALOGUE_OUTPUT = "dialogue_output"
    CAPABILITY_RESULT = "capability_result"


class ExperienceTarget(StrEnum):
    RESPONSE_LENGTH = "response_length"
    CONFIRMATION_POLICY = "confirmation_policy"


class ExperienceValue(StrEnum):
    BRIEF = "brief"
    DETAILED = "detailed"
    ASK_FIRST = "ask_first"
    RESPOND_NORMALLY = "respond_normally"


class ExperienceReaction(StrEnum):
    TOO_LONG = "too_long"
    TOO_SHORT = "too_short"
    WANTED_CONFIRMATION = "wanted_confirmation"
    DIRECT_RESPONSE_PREFERRED = "direct_response_preferred"
    NO_LONGER_APPLICABLE = "no_longer_applicable"


class ExperienceOperation(StrEnum):
    ADD = "add"
    REASSESS = "reassess"
    REJECT = "reject"
    CONFIRM = "confirm"
    CANCEL = "cancel"
    INVALID = "invalid"


class ExperienceChangeStatus(StrEnum):
    CONFIRMATION_REQUIRED = "confirmation_required"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    NOT_FOUND = "not_found"
    STALE = "stale"


REACTION_EFFECTS = {
    ExperienceReaction.TOO_LONG: (ExperienceTarget.RESPONSE_LENGTH, ExperienceValue.BRIEF),
    ExperienceReaction.TOO_SHORT: (ExperienceTarget.RESPONSE_LENGTH, ExperienceValue.DETAILED),
    ExperienceReaction.WANTED_CONFIRMATION: (ExperienceTarget.CONFIRMATION_POLICY, ExperienceValue.ASK_FIRST),
    ExperienceReaction.DIRECT_RESPONSE_PREFERRED: (ExperienceTarget.CONFIRMATION_POLICY, ExperienceValue.RESPOND_NORMALLY),
}


def _scope(words: tuple[str, ...]) -> None:
    if len(words) > 5 or len(set(words)) != len(words) or any(
        not word.strip() or len(word) > 32 or word in {"全般", "すべて", "全て", "*"}
        for word in words
    ):
        raise ValueError("experience scope must contain bounded explicit topics")


@dataclass(frozen=True, slots=True)
class ExperienceEvidence:
    evidence_id: str
    kind: ExperienceEvidenceKind
    counterpart_id: str
    context_id: str
    lifecycle_id: str
    source_reference: str
    observed_at: ExternalTime
    output_digest: str | None = None
    capability_status: CapabilityResultStatus | None = None
    capability_operation: CapabilityOperation | None = None
    capability_target: str | None = None

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (
            self.evidence_id, self.counterpart_id, self.context_id,
            self.lifecycle_id, self.source_reference,
        )):
            raise ValueError("experience observation identifiers are incomplete")
        capability = (self.capability_status, self.capability_operation, self.capability_target)
        if self.kind is ExperienceEvidenceKind.DIALOGUE_OUTPUT:
            if not self.output_digest or any(value is not None for value in capability):
                raise ValueError("dialogue evidence requires only a released output digest")
        elif self.output_digest is not None or any(value is None for value in capability) or not (self.capability_target or "").strip():
            raise ValueError("capability evidence requires its unchanged observed result")


@dataclass(frozen=True, slots=True)
class ExperienceItem:
    item_id: str
    version: int
    counterpart_id: str
    context_id: str
    target: ExperienceTarget
    value: ExperienceValue
    active: bool
    evidence_id: str
    feedback_source_reference: str
    confirmation_reference: str
    confirmed_at: ExternalTime
    reason: str
    scope_keywords: tuple[str, ...] = ()
    decision_kind: str = "dialogue"
    capability_operation: CapabilityOperation | None = None
    capability_target: str | None = None
    reevaluation_condition: str = "新しい公開応答への明示反応、訂正、または不採用の申告時に別入力で確認する"

    def __post_init__(self) -> None:
        if self.version < 1 or not all(value.strip() for value in (
            self.item_id, self.counterpart_id, self.context_id, self.evidence_id,
            self.feedback_source_reference, self.confirmation_reference,
            self.reason, self.reevaluation_condition,
        )):
            raise ValueError("confirmed experience is incomplete")
        if self.feedback_source_reference == self.confirmation_reference or self.decision_kind != "dialogue":
            raise ValueError("experience requires separate confirmation and bounded dialogue use")
        if (self.target, self.value) not in REACTION_EFFECTS.values():
            raise ValueError("experience adaptation is outside finite allowed effects")
        if (self.capability_operation is None) != (self.capability_target is None):
            raise ValueError("capability experience requires operation and target together")
        if self.capability_target is not None and (not self.capability_target.strip() or self.scope_keywords):
            raise ValueError("capability experience cannot generalize beyond its local target")
        _scope(self.scope_keywords)

    @property
    def instruction(self) -> str:
        return {
            ExperienceValue.BRIEF: "要点を短く伝える。必要な境界や不確実さは省略しない。",
            ExperienceValue.DETAILED: "理由と具体例を補って詳しく説明する。",
            ExperienceValue.ASK_FIRST: "回答可能な通常対話では先に対象や意図を確認する。",
            ExperienceValue.RESPOND_NORMALLY: "通常の判断に従って応答する。必要な確認は省略しない。",
        }[self.value]


@dataclass(frozen=True, slots=True)
class ExperienceChangeRequest:
    request_id: str
    lifecycle_id: str
    counterpart_id: str
    context_id: str
    operation: ExperienceOperation
    source_reference: str
    reason: str
    requested_at: ExternalTime
    reaction: ExperienceReaction | None = None
    evidence_id: str | None = None
    target_item_id: str | None = None
    expected_version: int | None = None
    pending_id: str | None = None
    scope_keywords: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (
            self.request_id, self.lifecycle_id, self.counterpart_id, self.context_id,
            self.source_reference, self.reason,
        )):
            raise ValueError("experience change request is incomplete")
        if self.operation in (ExperienceOperation.ADD, ExperienceOperation.REASSESS) and (
            self.reaction not in REACTION_EFFECTS or not self.evidence_id
        ):
            raise ValueError("experience proposal requires observation and explicit typed reaction")
        if self.operation in (ExperienceOperation.REASSESS, ExperienceOperation.REJECT) and not self.target_item_id:
            raise ValueError("experience update requires an item")
        if self.expected_version is not None and self.expected_version < 1:
            raise ValueError("experience expected version must be positive")
        _scope(self.scope_keywords)


@dataclass(frozen=True, slots=True)
class ExperiencePendingConfirmation:
    pending_id: str
    request: ExperienceChangeRequest
    expires_at: ExternalTime

    def __post_init__(self) -> None:
        if not self.pending_id.strip() or self.request.operation not in (
            ExperienceOperation.ADD, ExperienceOperation.REASSESS, ExperienceOperation.REJECT,
        ) or self.expires_at.value <= self.request.requested_at.value:
            raise ValueError("experience pending confirmation is invalid")


@dataclass(frozen=True, slots=True)
class ExperienceChangeRecord:
    record_id: str
    request_id: str
    lifecycle_id: str
    counterpart_id: str
    operation: ExperienceOperation
    status: ExperienceChangeStatus
    source_reference: str
    evidence_id: str | None
    before: ExperienceItem | None
    after: ExperienceItem | None
    pending_id: str | None
    reason: str
    impact_scope: tuple[str, ...]
    occurred_at: ExternalTime
    proposal: ExperienceChangeRequest | None = None

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (
            self.record_id, self.request_id, self.lifecycle_id,
            self.counterpart_id, self.source_reference, self.reason,
        )) or not self.impact_scope or any(not value.strip() for value in self.impact_scope):
            raise ValueError("experience change record is incomplete")
        if any(item is not None and item.counterpart_id != self.counterpart_id for item in (self.before, self.after)):
            raise ValueError("experience record crosses counterparts")
        if self.before is not None and self.after is not None and (
            self.before.item_id != self.after.item_id or self.after.version != self.before.version + 1
        ):
            raise ValueError("experience update must advance exactly one version")
        if self.after is not None and self.status is not ExperienceChangeStatus.COMPLETED:
            raise ValueError("only completed experience changes can have an after state")
        if self.proposal is not None and (
            self.proposal.counterpart_id != self.counterpart_id
            or self.proposal.operation not in (ExperienceOperation.ADD, ExperienceOperation.REASSESS, ExperienceOperation.REJECT)
            or self.proposal.requested_at.value > self.occurred_at.value
        ):
            raise ValueError("experience history contains an unrelated or future proposal")


@dataclass(frozen=True, slots=True)
class ExperienceSelection:
    item: ExperienceItem
    selection_reason: str

    def __post_init__(self) -> None:
        if not self.item.active or not self.selection_reason.strip():
            raise ValueError("experience selection requires active information and a reason")


@dataclass(frozen=True, slots=True)
class ExperienceWindow:
    current_input_reference: str
    context_id: str
    memory_version: int
    selections: tuple[ExperienceSelection, ...]
    max_items: int
    max_characters: int

    def __post_init__(self) -> None:
        if not self.current_input_reference.strip() or not self.context_id.strip() or self.memory_version < 1 or self.max_items < 0 or self.max_characters < 0:
            raise ValueError("experience selection bounds are invalid")
        if len(self.selections) > self.max_items or self.total_characters > self.max_characters or len({s.item.target for s in self.selections}) != len(self.selections):
            raise ValueError("experience selection exceeds limits or has conflicting effects")

    @property
    def total_characters(self) -> int:
        return sum(len(selection.item.instruction) for selection in self.selections)


@dataclass(frozen=True, slots=True)
class ExperienceUsage:
    lifecycle_id: str
    source_reference: str
    counterpart_id: str
    context_id: str
    item_versions: tuple[tuple[str, int], ...]
    occurred_at: ExternalTime

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (self.lifecycle_id, self.source_reference, self.counterpart_id, self.context_id)) or not self.item_versions:
            raise ValueError("experience usage is incomplete")
        if len({item_id for item_id, _ in self.item_versions}) != len(self.item_versions) or any(not item_id.strip() or version < 1 for item_id, version in self.item_versions):
            raise ValueError("experience usage versions are invalid")
