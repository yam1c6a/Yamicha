"""Independent stage-8 protection checks and separated fixed executors."""

from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from yamicha.body.persistence import SQLitePersistenceStore
from yamicha.contracts import (
    DialogueOutput,
    ExpressionArtifact,
    ExpressionReview,
    ExternalRepairRequest,
    ExternalTime,
    FixedProtectionObservation,
    FixedProtectionPermit,
    FixedProtectionRequest,
    FixedProtectionResult,
    IndependentReleaseVerification,
    InputDisposition,
    InputRejection,
    OutputReleaseStatus,
    ProtectionAuditKind,
    ProtectionAuditRecord,
    ProtectionDecision,
    ProtectionMode,
    ProtectionPersistenceSnapshot,
    ProtectionReleasePermit,
    ProtectionReleaseRequest,
    ProtectionReleaseProposal,
    RawTextInput,
    RecoveryEvidenceSource,
    RecoveryObservation,
    ValidatedTextInput,
)

from .stage7 import Stage7ProtectionBoundary


PROTECTION_DEFINITION_VERSION = "stage8-protection-v1"
FIXED_OBSERVER = "body-health-monitor"
FIXED_CONDITION = "normal-authority-unavailable"
FIXED_OPERATION_ID = "enter-protected-mode"
FIXED_TARGET = "body-protection-control"
FIXED_SCOPE = "normal-input-output-and-persistence"
FIXED_PROCEDURE = ("atomically-transition-normal-to-protected",)
FIXED_EXECUTION_SHAPE = "single-atomic-transition"
FIXED_COUNTER = "protection-execution-counter"
FIXED_STOP_CONDITION = "transition-completed-or-monitoring-unavailable"
FIXED_STOP_OBSERVER = "protection-stop-monitor"
FIXED_STOP_PROCEDURE = "invalidate-permit-and-stop-before-next-unit"
FIXED_STOP_ENFORCER = "independent-protection-stop-enforcer"
INDEPENDENT_RELEASE_VERIFIER = "independent-protection-release-verifier"


class ProtectionActiveError(RuntimeError):
    pass


class RegisteredRecoveryObserver:
    def __init__(
        self,
        source: RecoveryEvidenceSource,
        *,
        observation_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.source = source
        self._observation_id_factory = observation_id_factory or (
            lambda: str(uuid4())
        )
        self._issued: dict[str, RecoveryObservation] = {}

    def observe(
        self,
        *,
        healthy: bool,
        fact: str,
        uncertainty: str | None,
        observed_at: ExternalTime,
    ) -> RecoveryObservation:
        observation = RecoveryObservation(
            observation_id=self._observation_id_factory(),
            source=self.source,
            healthy=healthy,
            fact=fact,
            uncertainty=uncertainty,
            observed_at=observed_at,
        )
        self._issued[observation.observation_id] = observation
        return observation

    def issued(self, observation: RecoveryObservation) -> bool:
        return self._issued.get(observation.observation_id) == observation


class IndependentProtectionReleaseVerifier:
    def __init__(
        self,
        store: SQLitePersistenceStore,
        *,
        verification_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._store = store
        self._verification_id_factory = verification_id_factory or (
            lambda: str(uuid4())
        )
        self._issued: dict[str, IndependentReleaseVerification] = {}

    def verify(
        self,
        *,
        proposal: ProtectionReleaseProposal,
        observations: tuple[RecoveryObservation, ...],
        verified_at: ExternalTime,
    ) -> IndependentReleaseVerification:
        mode, definition_version, activation_id, _ = (
            self._store.protection_control_state()
        )
        self._store.protection_audit_records()
        required = {
            RecoveryEvidenceSource.BODY,
            RecoveryEvidenceSource.STATE,
            RecoveryEvidenceSource.AFFECTED_ORGAN,
        }
        passed = (
            mode is ProtectionMode.PROTECTED
            and activation_id == proposal.activation_id
            and definition_version == proposal.protection_definition_version
            and {observation.source for observation in observations} == required
            and len(observations) == len(required)
            and all(observation.healthy for observation in observations)
            and all(observation.uncertainty is None for observation in observations)
        )
        verification = IndependentReleaseVerification(
            verification_id=self._verification_id_factory(),
            verifier=INDEPENDENT_RELEASE_VERIFIER,
            activation_id=proposal.activation_id,
            passed=passed,
            verified_at=verified_at,
        )
        self._issued[verification.verification_id] = verification
        return verification

    def issued(self, verification: IndependentReleaseVerification) -> bool:
        return self._issued.get(verification.verification_id) == verification


class FixedProtectionObserver:
    def __init__(
        self,
        *,
        observation_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._observation_id_factory = observation_id_factory or (
            lambda: str(uuid4())
        )

    def observe_normal_authority_unavailable(
        self,
        observed_at: ExternalTime,
    ) -> FixedProtectionObservation:
        return FixedProtectionObservation(
            observation_id=self._observation_id_factory(),
            source=FIXED_OBSERVER,
            condition=FIXED_CONDITION,
            operation_id=FIXED_OPERATION_ID,
            observed_at=observed_at,
        )


class FixedProtectionCounter:
    def __init__(
        self,
        store: SQLitePersistenceStore,
        *,
        reservation_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._store = store
        self._reservation_id_factory = reservation_id_factory or (
            lambda: str(uuid4())
        )

    def reserve(self, observation: FixedProtectionObservation) -> str | None:
        reservation_id = self._reservation_id_factory()
        if not reservation_id.strip():
            raise ValueError("protection reservation ID must not be empty")
        if not self._store.reserve_protection_execution(
            observation_id=observation.observation_id,
            reservation_id=reservation_id,
            operation_id=observation.operation_id,
            reserved_at=observation.observed_at,
        ):
            return None
        return reservation_id


class FixedProtectionRequestFactory:
    def __init__(
        self,
        *,
        request_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._request_id_factory = request_id_factory or (lambda: str(uuid4()))

    def make(
        self,
        observation: FixedProtectionObservation,
        reservation_id: str,
    ) -> FixedProtectionRequest:
        return FixedProtectionRequest(
            request_id=self._request_id_factory(),
            observation=observation,
            definition_version=PROTECTION_DEFINITION_VERSION,
            operation_id=FIXED_OPERATION_ID,
            target=FIXED_TARGET,
            scope=FIXED_SCOPE,
            procedure=FIXED_PROCEDURE,
            execution_shape=FIXED_EXECUTION_SHAPE,
            counter_source=FIXED_COUNTER,
            reservation_id=reservation_id,
            stop_condition=FIXED_STOP_CONDITION,
            stop_observer=FIXED_STOP_OBSERVER,
            stop_procedure=FIXED_STOP_PROCEDURE,
            independent_stop_enforcer=FIXED_STOP_ENFORCER,
        )


class Stage8ProtectionBoundary(Stage7ProtectionBoundary):
    def __init__(
        self,
        *,
        store: SQLitePersistenceStore,
        authorized_input_sources: tuple[str, ...],
        max_text_length: int = 4096,
        normal_dialogue_output_enabled: bool = True,
        audit_id_factory: Callable[[], str] | None = None,
        permit_id_factory: Callable[[], str] | None = None,
        audit_time_factory: Callable[[], ExternalTime],
        recovery_observation_validators: dict[
            RecoveryEvidenceSource,
            Callable[[RecoveryObservation], bool],
        ],
        release_proposal_validator: Callable[
            [ProtectionReleaseProposal, tuple[RecoveryObservation, ...]],
            bool,
        ],
        independent_verification_validator: Callable[
            [IndependentReleaseVerification],
            bool,
        ],
    ) -> None:
        super().__init__(
            max_text_length=max_text_length,
            normal_dialogue_output_enabled=normal_dialogue_output_enabled,
        )
        if not authorized_input_sources or any(
            not source.strip() for source in authorized_input_sources
        ):
            raise ValueError("authorized input sources must not be empty")
        self._store = store
        self._authorized_input_sources = frozenset(authorized_input_sources)
        self._audit_id_factory = audit_id_factory or (lambda: str(uuid4()))
        self._permit_id_factory = permit_id_factory or (lambda: str(uuid4()))
        self._audit_time_factory = audit_time_factory
        self._recovery_observation_validators = dict(
            recovery_observation_validators
        )
        self._release_proposal_validator = release_proposal_validator
        self._independent_verification_validator = (
            independent_verification_validator
        )

    @property
    def mode(self) -> ProtectionMode:
        return self._store.protection_control_state()[0]

    @property
    def activation_id(self) -> str | None:
        return self._store.protection_control_state()[2]

    def validate(
        self,
        raw: RawTextInput,
    ) -> ValidatedTextInput | InputRejection:
        audit_time = (
            raw.received_at
            if isinstance(raw.received_at, ExternalTime)
            else self._audit_time_factory()
        )
        if self.mode is ProtectionMode.PROTECTED:
            rejection = InputRejection(
                input_id=(
                    raw.input_id if isinstance(raw.input_id, str) else "<invalid>"
                ),
                disposition=InputDisposition.BLOCKED,
                reason="normal input is blocked while protection is active",
            )
            self._audit(
                at=audit_time,
                kind=ProtectionAuditKind.INPUT_VALIDATION,
                actor="protection-boundary",
                target="text-input",
                decision=ProtectionDecision.REJECTED,
                reason=rejection.reason,
                correlation_id=rejection.input_id,
            )
            return rejection
        result = super().validate(raw)
        if not isinstance(result, InputRejection) and (
            result.source_id not in self._authorized_input_sources
        ):
            result = InputRejection(
                input_id=result.input_id,
                disposition=InputDisposition.UNAUTHORIZED,
                reason="verified input source has no permission for this channel",
            )
        self._audit(
            at=audit_time,
            kind=ProtectionAuditKind.INPUT_VALIDATION,
            actor="protection-boundary",
            target="text-input",
            decision=(
                ProtectionDecision.REJECTED
                if isinstance(result, InputRejection)
                else ProtectionDecision.ALLOWED
            ),
            reason=(
                result.reason
                if isinstance(result, InputRejection)
                else "format, source verification, and channel permission passed"
            ),
            correlation_id=result.input_id,
        )
        return result

    def release_dialogue_output(
        self,
        artifact: ExpressionArtifact,
        review: ExpressionReview,
    ) -> DialogueOutput:
        if self.mode is ProtectionMode.PROTECTED:
            output = DialogueOutput(
                lifecycle_id=artifact.lifecycle_id,
                artifact_id=artifact.artifact_id,
                status=OutputReleaseStatus.BLOCKED,
                text=None,
                reason="normal dialogue output is blocked while protection is active",
            )
        else:
            output = super().release_dialogue_output(artifact, review)
        self._audit(
            at=self._audit_time_factory(),
            kind=ProtectionAuditKind.PERMISSION,
            actor="protection-boundary",
            target="dialogue-output",
            decision=(
                ProtectionDecision.REJECTED
                if output.status is OutputReleaseStatus.BLOCKED
                else ProtectionDecision.ALLOWED
            ),
            reason=output.reason,
            correlation_id=artifact.lifecycle_id,
        )
        return output

    def authorize_persistence_update(
        self,
        *,
        correlation_id: str,
        owner_versions_present: bool,
        completed_lifecycle: bool,
        at: ExternalTime,
    ) -> bool:
        allowed = (
            self.mode is ProtectionMode.NORMAL
            and owner_versions_present
            and completed_lifecycle
        )
        self._audit(
            at=at,
            kind=ProtectionAuditKind.PERMISSION,
            actor="protection-boundary",
            target="persistence-update",
            decision=(
                ProtectionDecision.ALLOWED
                if allowed
                else ProtectionDecision.REJECTED
            ),
            reason=(
                "owner versions and completed lifecycle were verified"
                if allowed
                else "protection or persistence authorization evidence is incomplete"
            ),
            correlation_id=correlation_id,
        )
        return allowed

    def authorize_fixed_inward_operation(
        self,
        request: FixedProtectionRequest,
    ) -> FixedProtectionPermit | None:
        observation = request.observation
        matches = (
            self.mode is ProtectionMode.NORMAL
            and observation.source == FIXED_OBSERVER
            and observation.condition == FIXED_CONDITION
            and observation.operation_id == FIXED_OPERATION_ID
            and request.definition_version == PROTECTION_DEFINITION_VERSION
            and request.operation_id == FIXED_OPERATION_ID
            and request.target == FIXED_TARGET
            and request.scope == FIXED_SCOPE
            and request.procedure == FIXED_PROCEDURE
            and len(request.procedure) == 1
            and request.execution_shape == FIXED_EXECUTION_SHAPE
            and request.counter_source == FIXED_COUNTER
            and self._store.has_protection_reservation(
                observation_id=observation.observation_id,
                reservation_id=request.reservation_id,
                operation_id=request.operation_id,
            )
            and request.stop_condition == FIXED_STOP_CONDITION
            and request.stop_observer == FIXED_STOP_OBSERVER
            and request.stop_procedure == FIXED_STOP_PROCEDURE
            and request.independent_stop_enforcer == FIXED_STOP_ENFORCER
        )
        self._audit(
            at=observation.observed_at,
            kind=ProtectionAuditKind.PERMISSION,
            actor="protection-boundary",
            target=request.operation_id,
            decision=(
                ProtectionDecision.ALLOWED
                if matches
                else ProtectionDecision.REJECTED
            ),
            reason=(
                "fixed observation, operation, reservation, stop monitoring, and single transition matched"
                if matches
                else "fixed inward protection contract did not match"
            ),
            correlation_id=request.request_id,
        )
        if not matches:
            return None
        return FixedProtectionPermit(
            permit_id=self._permit_id_factory(),
            request=request,
            authorized_at=observation.observed_at,
        )

    def reject_external_repair_as_inward(
        self,
        request: ExternalRepairRequest,
    ) -> bool:
        self._audit(
            at=request.requested_at,
            kind=ProtectionAuditKind.EXTERNAL_REPAIR,
            actor="protection-boundary",
            target=request.destination,
            decision=ProtectionDecision.REJECTED,
            reason=(
                "external repair is not an inward fixed operation and has no stage-8 preapproval"
            ),
            correlation_id=request.request_id,
        )
        return False

    def authorize_release(
        self,
        request: ProtectionReleaseRequest,
    ) -> ProtectionReleasePermit | None:
        mode, definition_version, activation_id, _ = (
            self._store.protection_control_state()
        )
        sources = {observation.source for observation in request.observations}
        required_sources = {
            RecoveryEvidenceSource.BODY,
            RecoveryEvidenceSource.STATE,
            RecoveryEvidenceSource.AFFECTED_ORGAN,
        }
        proposal = request.proposal
        verification: IndependentReleaseVerification = request.verification
        matches = (
            mode is ProtectionMode.PROTECTED
            and activation_id is not None
            and proposal.activation_id == activation_id
            and verification.activation_id == activation_id
            and proposal.protection_definition_version == definition_version
            and sources == required_sources
            and len(request.observations) == len(required_sources)
            and all(observation.healthy for observation in request.observations)
            and all(
                observation.uncertainty is None
                for observation in request.observations
            )
            and all(
                observation.source in self._recovery_observation_validators
                and self._recovery_observation_validators[
                    observation.source
                ](observation)
                for observation in request.observations
            )
            and self._release_proposal_validator(
                proposal,
                request.observations,
            )
            and verification.verifier == INDEPENDENT_RELEASE_VERIFIER
            and verification.passed
            and self._independent_verification_validator(verification)
            and bool(proposal.judgment_approval_id.strip())
            and bool(proposal.core_finalization_id.strip())
        )
        if matches:
            self._store.protection_audit_records()
        self._audit(
            at=request.requested_at,
            kind=ProtectionAuditKind.RELEASE,
            actor="protection-boundary",
            target="normal-path",
            decision=(
                ProtectionDecision.ALLOWED
                if matches
                else ProtectionDecision.REJECTED
            ),
            reason=(
                "three recovery sources, internal proposal, independent verification, and history matched"
                if matches
                else "independent protection release conditions are incomplete"
            ),
            correlation_id=request.request_id,
        )
        if not matches:
            return None
        return ProtectionReleasePermit(
            permit_id=self._permit_id_factory(),
            request=request,
            authorized_at=request.requested_at,
        )

    def persistence_snapshot(self) -> ProtectionPersistenceSnapshot:
        mode, definition_version, activation_id, version = (
            self._store.protection_control_state()
        )
        return ProtectionPersistenceSnapshot(
            normal_dialogue_output_enabled=self._normal_dialogue_output_enabled,
            version=version,
            mode=mode,
            definition_version=definition_version,
            activation_id=activation_id,
        )

    def restore_owned_state(self, snapshot: ProtectionPersistenceSnapshot) -> None:
        self._normal_dialogue_output_enabled = (
            snapshot.normal_dialogue_output_enabled
        )

    def _audit(
        self,
        *,
        at: ExternalTime,
        kind: ProtectionAuditKind,
        actor: str,
        target: str,
        decision: ProtectionDecision,
        reason: str,
        correlation_id: str | None,
    ) -> None:
        self._store.append_protection_audit(
            ProtectionAuditRecord(
                record_id=self._audit_id_factory(),
                occurred_at=at,
                kind=kind,
                actor=actor,
                target=target,
                decision=decision,
                reason=reason,
                correlation_id=correlation_id,
            )
        )


class FixedInwardProtectionExecutor:
    def __init__(
        self,
        store: SQLitePersistenceStore,
        *,
        activation_id_factory: Callable[[], str] | None = None,
        audit_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._store = store
        self._activation_id_factory = activation_id_factory or (lambda: str(uuid4()))
        self._audit_id_factory = audit_id_factory or (lambda: str(uuid4()))

    def execute(self, permit: FixedProtectionPermit) -> FixedProtectionResult:
        request = permit.request
        if request.execution_shape != FIXED_EXECUTION_SHAPE or len(request.procedure) != 1:
            raise ValueError("fixed executor only accepts one atomic transition")
        activation_id = self._activation_id_factory()
        completed_at = permit.authorized_at
        self._store.activate_protection_atomic(
            observation_id=request.observation.observation_id,
            reservation_id=request.reservation_id,
            operation_id=request.operation_id,
            activation_id=activation_id,
            activated_at=completed_at,
            audit=ProtectionAuditRecord(
                record_id=self._audit_id_factory(),
                occurred_at=completed_at,
                kind=ProtectionAuditKind.ACTIVATION,
                actor="fixed-inward-protection-executor",
                target=request.target,
                decision=ProtectionDecision.COMPLETED,
                reason="one atomic normal-to-protected transition completed",
                correlation_id=request.request_id,
            ),
        )
        return FixedProtectionResult(
            operation_id=request.operation_id,
            activation_id=activation_id,
            previous_mode=ProtectionMode.NORMAL,
            current_mode=ProtectionMode.PROTECTED,
            completed_at=completed_at,
        )


class ProtectionReleaseExecutor:
    def __init__(
        self,
        store: SQLitePersistenceStore,
        *,
        audit_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._store = store
        self._audit_id_factory = audit_id_factory or (lambda: str(uuid4()))

    def execute(self, permit: ProtectionReleasePermit) -> None:
        request = permit.request
        self._store.release_protection_atomic(
            activation_id=request.proposal.activation_id,
            released_at=permit.authorized_at,
            audit=ProtectionAuditRecord(
                record_id=self._audit_id_factory(),
                occurred_at=permit.authorized_at,
                kind=ProtectionAuditKind.RELEASE,
                actor="independent-protection-release-executor",
                target="normal-path",
                decision=ProtectionDecision.COMPLETED,
                reason="independently authorized protection release completed",
                correlation_id=request.request_id,
            ),
        )
