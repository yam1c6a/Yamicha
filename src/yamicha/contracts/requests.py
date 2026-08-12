"""Traceable request and response contracts for Core-mediated routing."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from .input import InputDisposition, InputRejection, SensoryEvent
from .responsibilities import ResponsibilityId
from .time import ExternalTime


class RequestKind(StrEnum):
    REFERENCE = "reference"
    JUDGMENT_START = "judgment_start"


class RequestStatus(StrEnum):
    RECEIVED = "received"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RESULT_UNKNOWN = "result_unknown"


class Confidentiality(StrEnum):
    PRIVATE = "private"


@dataclass(frozen=True, slots=True)
class OrganRequest:
    request_id: str
    lifecycle_id: str
    kind: RequestKind
    source: ResponsibilityId
    destination: ResponsibilityId
    purpose: str
    target: str
    input_references: tuple[str, ...]
    expected_result: str
    authority_context: str
    confidentiality: Confidentiality
    created_at: ExternalTime
    deadline: ExternalTime
    causation_id: str

    def __post_init__(self) -> None:
        required = {
            "request_id": self.request_id,
            "lifecycle_id": self.lifecycle_id,
            "purpose": self.purpose,
            "target": self.target,
            "expected_result": self.expected_result,
            "authority_context": self.authority_context,
            "causation_id": self.causation_id,
        }
        empty = [name for name, value in required.items() if not value.strip()]
        if empty or not self.input_references:
            raise ValueError(
                f"organ request required values must not be empty: {empty}"
            )
        if self.deadline.value < self.created_at.value:
            raise ValueError("organ request deadline precedes creation time")


@dataclass(frozen=True, slots=True)
class OrganResponse:
    request_id: str
    responder: ResponsibilityId
    status: RequestStatus
    result_reference: str | None
    confirmed_effects: tuple[str, ...]
    unconfirmed_effects: tuple[str, ...]
    error: str | None
    occurred_at: ExternalTime
    uncertainty: str | None


class InputCycleStatus(StrEnum):
    ROUTED = "routed"
    DUPLICATE_RECORDED = "duplicate_recorded"


@dataclass(frozen=True, slots=True)
class RoutedInputCycle:
    lifecycle_id: str
    event: SensoryEvent
    status: InputCycleStatus
    requests: tuple[OrganRequest, ...]
    responses: tuple[OrganResponse, ...]


@dataclass(frozen=True, slots=True)
class InputProcessingOutcome:
    correlation_id: str
    disposition: InputDisposition
    rejection: InputRejection | None = None
    cycle: RoutedInputCycle | None = None

    def __post_init__(self) -> None:
        accepted = self.disposition in {
            InputDisposition.ACCEPTED,
            InputDisposition.DUPLICATE,
        }
        if accepted != (self.cycle is not None):
            raise ValueError("accepted input requires a routed cycle")
        if accepted == (self.rejection is not None):
            raise ValueError("rejected input requires only a rejection")


class ObservationKind(StrEnum):
    INTERNAL_STATE = "internal_state"
    UNFINISHED_MATTER = "unfinished_matter"
    RELATIONSHIP_NEED = "relationship_need"
    ANOMALY = "anomaly"


@dataclass(frozen=True, slots=True)
class ObservationEvidence:
    kind: ObservationKind
    reference: str
    observed_at: ExternalTime


@dataclass(frozen=True, slots=True)
class JudgmentStartRequest:
    request_id: str
    lifecycle_id: str
    source: ResponsibilityId
    purpose: str
    evidence: ObservationEvidence


@dataclass(frozen=True, slots=True)
class RegisteredJudgmentStart:
    request: JudgmentStartRequest
    status: RequestStatus


class ReferenceReader(Protocol):
    definition: object

    def read_reference(self, request: OrganRequest) -> OrganResponse:
        """Validate and answer one read-only request routed through Core."""
        ...
