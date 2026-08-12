"""Contracts for stage-5 expression, review, and dialogue output."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .input import InputDisposition
from .judgment import FinalizationStatus, Stage4InputOutcome
from .lifecycle import DecisionDirection


class ExpressionMode(StrEnum):
    RESPONSE = "response"
    CONFIRMATION_REQUEST = "confirmation_request"
    HOLD_NOTICE = "hold_notice"
    REFUSAL_NOTICE = "refusal_notice"
    SILENCE = "silence"


def expression_mode_for(direction: DecisionDirection) -> ExpressionMode:
    return {
        DecisionDirection.RESPOND: ExpressionMode.RESPONSE,
        DecisionDirection.CONFIRM: ExpressionMode.CONFIRMATION_REQUEST,
        DecisionDirection.HOLD: ExpressionMode.HOLD_NOTICE,
        DecisionDirection.REFUSE: ExpressionMode.REFUSAL_NOTICE,
        DecisionDirection.WAIT: ExpressionMode.SILENCE,
        DecisionDirection.NO_ACTION: ExpressionMode.SILENCE,
    }[direction]


class StatementKind(StrEnum):
    FACT = "fact"
    INFERENCE = "inference"
    UNKNOWN = "unknown"
    CONFIRMATION_REQUEST = "confirmation_request"
    REFUSAL = "refusal"
    HOLD = "hold"


class ExpressionMeaning(StrEnum):
    INPUT_RECEIVED = "input_received"
    EXTERNAL_EFFECT_POSSIBLE = "external_effect_possible"
    EXTERNAL_EFFECT_NOT_EXECUTED = "external_effect_not_executed"
    TARGET_AUTHORITY_EFFECT_UNKNOWN = "target_authority_effect_unknown"
    CONFIRM_TARGET_AUTHORITY_EFFECT = "confirm_target_authority_effect"
    MATERIAL_INSUFFICIENT = "material_insufficient"
    DECISION_HELD = "decision_held"
    BOUNDARY_VIOLATION = "boundary_violation"
    REQUEST_REFUSED = "request_refused"


_MEANING_KINDS = {
    ExpressionMeaning.INPUT_RECEIVED: StatementKind.FACT,
    ExpressionMeaning.EXTERNAL_EFFECT_POSSIBLE: StatementKind.INFERENCE,
    ExpressionMeaning.EXTERNAL_EFFECT_NOT_EXECUTED: StatementKind.FACT,
    ExpressionMeaning.TARGET_AUTHORITY_EFFECT_UNKNOWN: StatementKind.UNKNOWN,
    ExpressionMeaning.CONFIRM_TARGET_AUTHORITY_EFFECT: (
        StatementKind.CONFIRMATION_REQUEST
    ),
    ExpressionMeaning.MATERIAL_INSUFFICIENT: StatementKind.UNKNOWN,
    ExpressionMeaning.DECISION_HELD: StatementKind.HOLD,
    ExpressionMeaning.BOUNDARY_VIOLATION: StatementKind.FACT,
    ExpressionMeaning.REQUEST_REFUSED: StatementKind.REFUSAL,
}


@dataclass(frozen=True, slots=True)
class ExpressionItem:
    kind: StatementKind
    meaning: ExpressionMeaning
    source_reference: str

    def __post_init__(self) -> None:
        if self.kind is not _MEANING_KINDS[self.meaning]:
            raise ValueError("expression meaning and statement kind do not match")
        if not self.source_reference.strip():
            raise ValueError("expression item requires a source reference")


@dataclass(frozen=True, slots=True)
class ExpressionRequest:
    request_id: str
    lifecycle_id: str
    decision_reference: str
    direction: DecisionDirection
    mode: ExpressionMode
    items: tuple[ExpressionItem, ...]
    silence_required: bool
    confirmed_effects: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not all(
            value.strip()
            for value in (
                self.request_id,
                self.lifecycle_id,
                self.decision_reference,
            )
        ):
            raise ValueError("expression request identifiers must not be empty")
        if self.mode is not expression_mode_for(self.direction):
            raise ValueError("expression mode must preserve the finalized direction")
        silent = self.mode is ExpressionMode.SILENCE
        if self.silence_required is not silent:
            raise ValueError("silence requirement must match expression mode")
        if silent != (not self.items):
            raise ValueError("only silence has no expression items")


@dataclass(frozen=True, slots=True)
class RenderedStatement:
    item: ExpressionItem
    text: str

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("rendered statement text must not be empty")


@dataclass(frozen=True, slots=True)
class ExpressionArtifact:
    artifact_id: str
    request_id: str
    lifecycle_id: str
    direction: DecisionDirection
    mode: ExpressionMode
    text: str | None
    statements: tuple[RenderedStatement, ...]
    claimed_completed_effects: tuple[str, ...] = ()
    external_intelligence_used: bool = False

    def __post_init__(self) -> None:
        if not all(
            value.strip()
            for value in (self.artifact_id, self.request_id, self.lifecycle_id)
        ):
            raise ValueError("expression artifact identifiers must not be empty")
        if self.mode is not expression_mode_for(self.direction):
            raise ValueError("expression artifact cannot change decision direction")
        silent = self.mode is ExpressionMode.SILENCE
        if silent:
            if self.text is not None or self.statements:
                raise ValueError("silent expression must not contain output text")
        elif self.text is None or not self.text.strip() or not self.statements:
            raise ValueError("non-silent expression requires rendered statements")


class ExpressionReviewStatus(StrEnum):
    ACCEPTED = "accepted"
    REEXPRESSION_REQUIRED = "reexpression_required"


@dataclass(frozen=True, slots=True)
class ExpressionReview:
    lifecycle_id: str
    request_id: str
    artifact_id: str
    direction: DecisionDirection
    status: ExpressionReviewStatus
    reason: str

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError("expression review requires a reason")


class OutputReleaseStatus(StrEnum):
    RELEASED = "released"
    SILENT = "silent"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class DialogueOutput:
    lifecycle_id: str
    artifact_id: str
    status: OutputReleaseStatus
    text: str | None
    reason: str

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError("dialogue output requires a reason")
        released = self.status is OutputReleaseStatus.RELEASED
        if released != (self.text is not None and bool(self.text.strip())):
            raise ValueError("only released dialogue output contains text")


@dataclass(frozen=True, slots=True)
class Stage5InputOutcome(Stage4InputOutcome):
    expression_request: ExpressionRequest | None = None
    expression: ExpressionArtifact | None = None
    expression_review: ExpressionReview | None = None
    dialogue_output: DialogueOutput | None = None

    def __post_init__(self) -> None:
        Stage4InputOutcome.__post_init__(self)
        expression_values = (
            self.expression_request,
            self.expression,
            self.expression_review,
            self.dialogue_output,
        )
        has_expression_path = all(value is not None for value in expression_values)
        no_expression_path = all(value is None for value in expression_values)
        if not has_expression_path and not no_expression_path:
            raise ValueError("stage-5 expression path must be complete or absent")
        should_express = (
            self.disposition is InputDisposition.ACCEPTED
            and self.finalization is not None
            and self.finalization.status is FinalizationStatus.FINALIZED
        )
        if has_expression_path is not should_express:
            raise ValueError("only finalized accepted input reaches expression")
