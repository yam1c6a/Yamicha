"""Stage-9 contracts for one bounded read-only capability."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .time import ExternalTime


READ_ONLY_EXPECTED_EFFECT = "read-only:no-external-state-change"


class CapabilityOperation(StrEnum):
    READ_TEXT = "read_text"


class CapabilityResultStatus(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    PARTIAL_SUCCESS = "partial_success"
    UNKNOWN = "unknown"


class CapabilityDispatchStatus(StrEnum):
    EXECUTED = "executed"
    REJECTED = "rejected"
    DUPLICATE = "duplicate"


@dataclass(frozen=True, slots=True)
class CapabilityUseProposal:
    proposal_id: str
    target: str
    operation: CapabilityOperation
    authority_id: str
    expected_effect: str
    idempotency_key: str
    reason: str
    verification_required: bool
    proposed_at: ExternalTime

    def __post_init__(self) -> None:
        required = (
            self.proposal_id,
            self.target,
            self.authority_id,
            self.expected_effect,
            self.idempotency_key,
            self.reason,
        )
        if not all(value.strip() for value in required):
            raise ValueError("capability proposal is incomplete")


@dataclass(frozen=True, slots=True)
class IntegratedCapabilityRequest:
    request_id: str
    proposal: CapabilityUseProposal
    core_finalization_id: str
    integrated_at: ExternalTime

    def __post_init__(self) -> None:
        if not self.request_id.strip() or not self.core_finalization_id.strip():
            raise ValueError("integrated capability request is incomplete")

    @property
    def target(self) -> str:
        return self.proposal.target

    @property
    def operation(self) -> CapabilityOperation:
        return self.proposal.operation

    @property
    def authority_id(self) -> str:
        return self.proposal.authority_id

    @property
    def expected_effect(self) -> str:
        return self.proposal.expected_effect

    @property
    def idempotency_key(self) -> str:
        return self.proposal.idempotency_key


@dataclass(frozen=True, slots=True)
class CapabilityPermissionObservation:
    observation_id: str
    observer: str
    authority_id: str
    target: str
    operation: CapabilityOperation
    granted: bool
    observed_at: ExternalTime

    def __post_init__(self) -> None:
        required = (
            self.observation_id,
            self.observer,
            self.authority_id,
            self.target,
        )
        if not all(value.strip() for value in required):
            raise ValueError("capability permission observation is incomplete")


@dataclass(frozen=True, slots=True)
class CapabilityExecutionPermit:
    permit_id: str
    request: IntegratedCapabilityRequest
    permission: CapabilityPermissionObservation
    authorized_at: ExternalTime

    def __post_init__(self) -> None:
        if not self.permit_id.strip():
            raise ValueError("capability execution permit ID must not be empty")


@dataclass(frozen=True, slots=True)
class ReadOnlyToolResult:
    status: CapabilityResultStatus
    content: str | None
    observed_scope: str
    remaining_scope: str | None
    detail: str
    uncertainty: str | None = None

    def __post_init__(self) -> None:
        if not self.observed_scope.strip() or not self.detail.strip():
            raise ValueError("read-only tool result is incomplete")
        if self.status is CapabilityResultStatus.SUCCESS:
            if self.content is None or self.remaining_scope is not None:
                raise ValueError("successful read result must be complete")
        elif self.status is CapabilityResultStatus.PARTIAL_SUCCESS:
            if self.content is None or not (self.remaining_scope or "").strip():
                raise ValueError("partial read result must identify remaining scope")
        elif self.status is CapabilityResultStatus.UNKNOWN:
            if not (self.uncertainty or "").strip():
                raise ValueError("unknown read result must state uncertainty")


@dataclass(frozen=True, slots=True)
class CapabilityResult:
    result_id: str
    request_id: str
    idempotency_key: str
    target: str
    operation: CapabilityOperation
    status: CapabilityResultStatus
    content: str | None
    observed_scope: str
    remaining_scope: str | None
    detail: str
    uncertainty: str | None
    completed_at: ExternalTime

    def __post_init__(self) -> None:
        required = (
            self.result_id,
            self.request_id,
            self.idempotency_key,
            self.target,
            self.observed_scope,
            self.detail,
        )
        if not all(value.strip() for value in required):
            raise ValueError("capability result is incomplete")
        ReadOnlyToolResult(
            status=self.status,
            content=self.content,
            observed_scope=self.observed_scope,
            remaining_scope=self.remaining_scope,
            detail=self.detail,
            uncertainty=self.uncertainty,
        )


@dataclass(frozen=True, slots=True)
class CapabilityResultEvent:
    event_id: str
    result: CapabilityResult
    received_at: ExternalTime

    def __post_init__(self) -> None:
        if not self.event_id.strip():
            raise ValueError("capability result event ID must not be empty")


@dataclass(frozen=True, slots=True)
class CapabilityExecutionRecord:
    idempotency_key: str
    request_id: str
    request_fingerprint: str
    status: str
    result_status: CapabilityResultStatus | None
    reserved_at: ExternalTime
    completed_at: ExternalTime | None

    def __post_init__(self) -> None:
        required = (
            self.idempotency_key,
            self.request_id,
            self.request_fingerprint,
            self.status,
        )
        if not all(value.strip() for value in required):
            raise ValueError("capability execution record is incomplete")
        if self.status not in {"reserved", "completed"}:
            raise ValueError("capability execution record status is invalid")
        if self.status == "reserved" and (
            self.result_status is not None or self.completed_at is not None
        ):
            raise ValueError("reserved capability execution cannot have a result")
        if self.status == "completed" and (
            self.result_status is None or self.completed_at is None
        ):
            raise ValueError("completed capability execution requires a result")


@dataclass(frozen=True, slots=True)
class Stage9CapabilityOutcome:
    dispatch_status: CapabilityDispatchStatus
    request: IntegratedCapabilityRequest
    result: CapabilityResult | None = None
    event: CapabilityResultEvent | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.dispatch_status is CapabilityDispatchStatus.EXECUTED:
            if self.result is None or self.event is None or self.reason is not None:
                raise ValueError("executed capability outcome is incomplete")
        elif self.result is not None or self.event is not None:
            raise ValueError("unexecuted capability outcome cannot contain a result")
        elif not (self.reason or "").strip():
            raise ValueError("unexecuted capability outcome requires a reason")
