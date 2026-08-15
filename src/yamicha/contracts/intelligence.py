"""Stage-10 contracts for bounded auxiliary-intelligence use."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .retention import Stage6InputOutcome
from .time import ExternalTime
from .dialogue import DialogueContextWindow


class IntelligencePurpose(StrEnum):
    DIALOGUE_RESPONSE_CANDIDATE = "dialogue_response_candidate"


class IntelligenceResultStatus(StrEnum):
    SUCCESS = "success"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    INVALID_OUTPUT = "invalid_output"
    CONSTRAINT_VIOLATION = "constraint_violation"


class IntelligenceAdoptionStatus(StrEnum):
    ADOPTED = "adopted"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class IntelligenceConstraints:
    max_input_characters: int
    max_output_characters: int
    timeout_seconds: float
    allowed_input_scope: tuple[str, ...]
    output_format: str
    speaker_name: str
    forbidden_self_identification: tuple[str, ...]
    external_effect_claims_allowed: bool = False

    def __post_init__(self) -> None:
        if (
            self.max_input_characters <= 0
            or self.max_output_characters <= 0
            or self.timeout_seconds <= 0
            or not self.allowed_input_scope
            or not all(value.strip() for value in self.allowed_input_scope)
            or not self.output_format.strip()
            or not self.speaker_name.strip()
            or not self.forbidden_self_identification
            or not all(
                value.strip() for value in self.forbidden_self_identification
            )
        ):
            raise ValueError("intelligence constraints are incomplete")


@dataclass(frozen=True, slots=True)
class AuxiliaryIntelligenceProposal:
    proposal_id: str
    lifecycle_id: str
    purpose: IntelligencePurpose
    model: str
    input_text: str
    input_source_reference: str
    constraints: IntelligenceConstraints
    proposed_at: ExternalTime
    dialogue_context: DialogueContextWindow | None = None

    def __post_init__(self) -> None:
        required = (
            self.proposal_id,
            self.lifecycle_id,
            self.model,
            self.input_text,
            self.input_source_reference,
        )
        if not all(value.strip() for value in required):
            raise ValueError("auxiliary-intelligence proposal is incomplete")
        input_characters = len(self.input_text)
        if self.dialogue_context is not None:
            if (
                self.dialogue_context.current_input_reference
                != self.input_source_reference
                or self.dialogue_context.current_input_characters
                != len(self.input_text)
            ):
                raise ValueError("dialogue context does not match the current input")
            input_characters = self.dialogue_context.total_characters
        if input_characters > self.constraints.max_input_characters:
            raise ValueError("intelligence proposal exceeds its input limit")


@dataclass(frozen=True, slots=True)
class IntegratedIntelligenceRequest:
    request_id: str
    proposal: AuxiliaryIntelligenceProposal
    core_authorization_id: str
    integrated_at: ExternalTime

    def __post_init__(self) -> None:
        if not self.request_id.strip() or not self.core_authorization_id.strip():
            raise ValueError("integrated intelligence request is incomplete")


@dataclass(frozen=True, slots=True)
class ExternalIntelligenceResponse:
    status: IntelligenceResultStatus
    model: str
    content: str | None
    detail: str

    def __post_init__(self) -> None:
        if not self.model.strip() or not self.detail.strip():
            raise ValueError("external intelligence response is incomplete")
        if self.status is IntelligenceResultStatus.SUCCESS:
            if self.content is None or not self.content.strip():
                raise ValueError("successful intelligence response requires content")
        elif self.content is not None:
            raise ValueError("failed intelligence response cannot contain content")


@dataclass(frozen=True, slots=True)
class IntelligenceCandidate:
    candidate_id: str
    request_id: str
    text: str
    model: str
    provenance: str
    unverified: bool = True

    def __post_init__(self) -> None:
        required = (
            self.candidate_id,
            self.request_id,
            self.text,
            self.model,
            self.provenance,
        )
        if not all(value.strip() for value in required):
            raise ValueError("intelligence candidate is incomplete")
        if not self.unverified:
            raise ValueError("external intelligence candidate must remain unverified")


@dataclass(frozen=True, slots=True)
class AuxiliaryIntelligenceResult:
    result_id: str
    request_id: str
    lifecycle_id: str
    purpose: IntelligencePurpose
    status: IntelligenceResultStatus
    model: str
    candidate: IntelligenceCandidate | None
    detail: str
    completed_at: ExternalTime

    def __post_init__(self) -> None:
        required = (
            self.result_id,
            self.request_id,
            self.lifecycle_id,
            self.model,
            self.detail,
        )
        if not all(value.strip() for value in required):
            raise ValueError("auxiliary-intelligence result is incomplete")
        if (self.status is IntelligenceResultStatus.SUCCESS) != (
            self.candidate is not None
        ):
            raise ValueError("only a successful intelligence result has a candidate")


@dataclass(frozen=True, slots=True)
class IntelligenceCandidateReview:
    review_id: str
    request_id: str
    result_id: str
    candidate_id: str | None
    accepted: bool
    reason: str
    reviewed_at: ExternalTime

    def __post_init__(self) -> None:
        if not all(
            value.strip() for value in (self.review_id, self.request_id, self.result_id)
        ) or not self.reason.strip():
            raise ValueError("intelligence candidate review is incomplete")
        if self.accepted != (self.candidate_id is not None):
            raise ValueError("accepted review must identify one candidate")


@dataclass(frozen=True, slots=True)
class IntelligenceAdoption:
    adoption_id: str
    lifecycle_id: str
    request_id: str
    result_id: str
    review_id: str
    candidate_id: str | None
    status: IntelligenceAdoptionStatus
    reason: str
    decided_at: ExternalTime

    def __post_init__(self) -> None:
        required = (
            self.adoption_id,
            self.lifecycle_id,
            self.request_id,
            self.result_id,
            self.review_id,
            self.reason,
        )
        if not all(value.strip() for value in required):
            raise ValueError("intelligence adoption is incomplete")
        if (self.status is IntelligenceAdoptionStatus.ADOPTED) != (
            self.candidate_id is not None
        ):
            raise ValueError("adopted intelligence output must identify a candidate")


@dataclass(frozen=True, slots=True)
class IntelligenceTraceRecord:
    trace_id: str
    request_id: str
    lifecycle_id: str
    purpose: IntelligencePurpose
    model: str
    input_scope: tuple[str, ...]
    input_digest: str
    constraints_digest: str
    result_status: IntelligenceResultStatus
    output_digest: str | None
    adoption_status: IntelligenceAdoptionStatus
    reason: str
    occurred_at: ExternalTime

    def __post_init__(self) -> None:
        required = (
            self.trace_id,
            self.request_id,
            self.lifecycle_id,
            self.model,
            self.input_digest,
            self.constraints_digest,
            self.reason,
        )
        if not all(value.strip() for value in required) or not self.input_scope:
            raise ValueError("intelligence trace is incomplete")
        if self.output_digest is not None and not self.output_digest.strip():
            raise ValueError("intelligence output digest must not be blank")


@dataclass(frozen=True, slots=True)
class Stage10InputOutcome(Stage6InputOutcome):
    intelligence_request: IntegratedIntelligenceRequest | None = None
    intelligence_result: AuxiliaryIntelligenceResult | None = None
    intelligence_review: IntelligenceCandidateReview | None = None
    intelligence_adoption: IntelligenceAdoption | None = None

    def __post_init__(self) -> None:
        Stage6InputOutcome.__post_init__(self)
        values = (
            self.intelligence_request,
            self.intelligence_result,
            self.intelligence_review,
            self.intelligence_adoption,
        )
        if not (all(value is None for value in values) or all(value is not None for value in values)):
            raise ValueError("stage-10 intelligence path must be complete or absent")
