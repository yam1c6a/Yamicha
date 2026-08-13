"""Stage-8 contracts for fixed protection, audit, and independent release."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .time import ExternalTime


class ProtectionMode(StrEnum):
    NORMAL = "normal"
    PROTECTED = "protected"


class ProtectionAuditKind(StrEnum):
    INPUT_VALIDATION = "input_validation"
    PERMISSION = "permission"
    ACTIVATION = "activation"
    FIXED_OPERATION = "fixed_operation"
    EXTERNAL_REPAIR = "external_repair"
    RELEASE = "release"


class ProtectionDecision(StrEnum):
    ALLOWED = "allowed"
    REJECTED = "rejected"
    COMPLETED = "completed"


class RecoveryEvidenceSource(StrEnum):
    BODY = "body"
    STATE = "state"
    AFFECTED_ORGAN = "affected_organ"


@dataclass(frozen=True, slots=True)
class ProtectionAuditRecord:
    record_id: str
    occurred_at: ExternalTime
    kind: ProtectionAuditKind
    actor: str
    target: str
    decision: ProtectionDecision
    reason: str
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        required = (self.record_id, self.actor, self.target, self.reason)
        if not all(value.strip() for value in required):
            raise ValueError("protection audit required values must not be empty")
        if self.correlation_id is not None and not self.correlation_id.strip():
            raise ValueError("audit correlation ID must not be blank")


@dataclass(frozen=True, slots=True)
class FixedProtectionObservation:
    observation_id: str
    source: str
    condition: str
    operation_id: str
    observed_at: ExternalTime

    def __post_init__(self) -> None:
        if not all(
            value.strip()
            for value in (
                self.observation_id,
                self.source,
                self.condition,
                self.operation_id,
            )
        ):
            raise ValueError("fixed protection observation is incomplete")


@dataclass(frozen=True, slots=True)
class FixedProtectionRequest:
    request_id: str
    observation: FixedProtectionObservation
    definition_version: str
    operation_id: str
    target: str
    scope: str
    procedure: tuple[str, ...]
    execution_shape: str
    counter_source: str
    reservation_id: str
    stop_condition: str
    stop_observer: str
    stop_procedure: str
    independent_stop_enforcer: str

    def __post_init__(self) -> None:
        strings = (
            self.request_id,
            self.definition_version,
            self.operation_id,
            self.target,
            self.scope,
            self.execution_shape,
            self.counter_source,
            self.reservation_id,
            self.stop_condition,
            self.stop_observer,
            self.stop_procedure,
            self.independent_stop_enforcer,
        )
        if not all(value.strip() for value in strings) or not self.procedure:
            raise ValueError("fixed protection request is incomplete")


@dataclass(frozen=True, slots=True)
class FixedProtectionPermit:
    permit_id: str
    request: FixedProtectionRequest
    authorized_at: ExternalTime

    def __post_init__(self) -> None:
        if not self.permit_id.strip():
            raise ValueError("fixed protection permit ID must not be empty")


@dataclass(frozen=True, slots=True)
class FixedProtectionResult:
    operation_id: str
    activation_id: str
    previous_mode: ProtectionMode
    current_mode: ProtectionMode
    completed_at: ExternalTime

    def __post_init__(self) -> None:
        if not self.operation_id.strip() or not self.activation_id.strip():
            raise ValueError("fixed protection result IDs must not be empty")
        if (
            self.previous_mode is not ProtectionMode.NORMAL
            or self.current_mode is not ProtectionMode.PROTECTED
        ):
            raise ValueError("fixed operation must be one atomic protection transition")


@dataclass(frozen=True, slots=True)
class ExternalRepairRequest:
    request_id: str
    destination: str
    requested_operation: str
    requested_at: ExternalTime

    def __post_init__(self) -> None:
        if not all(
            value.strip()
            for value in (
                self.request_id,
                self.destination,
                self.requested_operation,
            )
        ):
            raise ValueError("external repair request is incomplete")


@dataclass(frozen=True, slots=True)
class RecoveryObservation:
    observation_id: str
    source: RecoveryEvidenceSource
    healthy: bool
    fact: str
    uncertainty: str | None
    observed_at: ExternalTime

    def __post_init__(self) -> None:
        if not self.observation_id.strip() or not self.fact.strip():
            raise ValueError("recovery observation requires an ID and fact")
        if self.uncertainty is not None and not self.uncertainty.strip():
            raise ValueError("recovery uncertainty must not be blank")


@dataclass(frozen=True, slots=True)
class ProtectionReleaseEvaluation:
    evaluation_id: str
    activation_id: str
    protection_definition_version: str
    observation_ids: tuple[str, ...]
    evaluated_at: ExternalTime

    def __post_init__(self) -> None:
        if not all(
            value.strip()
            for value in (
                self.evaluation_id,
                self.activation_id,
                self.protection_definition_version,
            )
        ) or not self.observation_ids:
            raise ValueError("protection release evaluation is incomplete")


@dataclass(frozen=True, slots=True)
class ProtectionReleaseProposal:
    proposal_id: str
    activation_id: str
    protection_definition_version: str
    judgment_approval_id: str
    core_finalization_id: str
    created_at: ExternalTime

    def __post_init__(self) -> None:
        if not all(
            value.strip()
            for value in (
                self.proposal_id,
                self.activation_id,
                self.protection_definition_version,
                self.judgment_approval_id,
                self.core_finalization_id,
            )
        ):
            raise ValueError("protection release proposal is incomplete")


@dataclass(frozen=True, slots=True)
class IndependentReleaseVerification:
    verification_id: str
    verifier: str
    activation_id: str
    passed: bool
    verified_at: ExternalTime

    def __post_init__(self) -> None:
        if not all(
            value.strip()
            for value in (
                self.verification_id,
                self.verifier,
                self.activation_id,
            )
        ):
            raise ValueError("independent release verification is incomplete")


@dataclass(frozen=True, slots=True)
class ProtectionReleaseRequest:
    request_id: str
    proposal: ProtectionReleaseProposal
    observations: tuple[RecoveryObservation, ...]
    verification: IndependentReleaseVerification
    requested_at: ExternalTime

    def __post_init__(self) -> None:
        if not self.request_id.strip() or not self.observations:
            raise ValueError("protection release request is incomplete")


@dataclass(frozen=True, slots=True)
class ProtectionReleasePermit:
    permit_id: str
    request: ProtectionReleaseRequest
    authorized_at: ExternalTime

    def __post_init__(self) -> None:
        if not self.permit_id.strip():
            raise ValueError("protection release permit ID must not be empty")
