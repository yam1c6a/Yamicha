"""Compose stage-8 fixed protection, audit, and independent release."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar
from uuid import uuid4

from yamicha.body.external_effect_gate import ExternalEffectGateStub
from yamicha.body.persistence import PersistenceConsistencyError, SQLitePersistenceStore
from yamicha.body.protection_boundary import (
    FixedInwardProtectionExecutor,
    FixedProtectionCounter,
    FixedProtectionObserver,
    FixedProtectionRequestFactory,
    IndependentProtectionReleaseVerifier,
    ProtectionActiveError,
    ProtectionReleaseExecutor,
    RegisteredRecoveryObserver,
    Stage8ProtectionBoundary,
)
from yamicha.body.protection_boundary.stage8 import PROTECTION_DEFINITION_VERSION
from yamicha.body.runtime import Clock, RuntimeStatus, Stage2Runtime
from yamicha.contracts import (
    CycleOutcome,
    ExecutionOpportunity,
    ExternalRepairRequest,
    ExternalTime,
    FixedProtectionResult,
    OperatingState,
    PersistenceOpenResult,
    PersistenceSnapshot,
    ProtectionMode,
    ProtectionReleaseProposal,
    ProtectionReleaseRequest,
    RawTextInput,
    Stage6InputOutcome,
    RecoveryEvidenceSource,
    RecoveryObservation,
)
from yamicha.life.stage3 import Stage3Sensation
from yamicha.life.stage5 import Stage5Language
from yamicha.life.stage7 import (
    Stage7Memory,
    Stage7Relationship,
    Stage7State,
)
from yamicha.life.stage8 import Stage8Core, Stage8Judgment
from yamicha.life.stubs import AuxiliaryIntelligenceStub, CapabilityStub

from .composition import YamichaComposition
from .stage2 import InputFreeCycleRunner
from .stage7 import (
    Stage7SnapshotCoordinator,
    Stage7System,
    Stage7TextInputRunner,
)


CONFIGURATION_VERSION = "stage8-v1"


class Stage8SnapshotCoordinator(Stage7SnapshotCoordinator):
    def __init__(
        self,
        *,
        protection_boundary: Stage8ProtectionBoundary,
        state: Stage7State,
        memory: Stage7Memory,
        store: SQLitePersistenceStore,
        recovery: PersistenceOpenResult,
        core: Stage8Core,
        relationship: Stage7Relationship,
        snapshot_id_factory: Callable[[], str] | None = None,
    ) -> None:
        super().__init__(
            protection_boundary=protection_boundary,
            state=state,
            memory=memory,
            store=store,
            recovery=recovery,
            core=core,
            relationship=relationship,
            snapshot_id_factory=snapshot_id_factory,
        )
        self._stage8_protection_boundary = protection_boundary
        self._stage8_state = state
        self._stage8_memory = memory

    def commit(self, created_at: ExternalTime) -> PersistenceSnapshot:
        state_snapshot = self._stage8_state.persistence_snapshot()
        memory_snapshot = self._stage8_memory.persistence_snapshot()
        owner_versions_present = (
            state_snapshot.material_version > 0
            and memory_snapshot.material_version > 0
        )
        if not self._stage8_protection_boundary.authorize_persistence_update(
            correlation_id=state_snapshot.last_correlation_id,
            owner_versions_present=owner_versions_present,
            completed_lifecycle=(
                state_snapshot.operating_state is OperatingState.WAITING
            ),
            at=created_at,
        ):
            raise ProtectionActiveError(
                "normal persistence update was rejected by the protection boundary"
            )
        return super().commit(created_at)


class Stage8TextInputRunner(Stage7TextInputRunner):
    pass


@dataclass(slots=True, kw_only=True)
class Stage8System(Stage7System):
    stage_label: ClassVar[int] = 8
    protection_boundary: Stage8ProtectionBoundary
    core: Stage8Core
    judgment: Stage8Judgment
    snapshot_coordinator: Stage8SnapshotCoordinator
    text_input_runner: Stage8TextInputRunner
    fixed_observer: FixedProtectionObserver
    fixed_counter: FixedProtectionCounter
    fixed_request_factory: FixedProtectionRequestFactory
    fixed_executor: FixedInwardProtectionExecutor
    release_executor: ProtectionReleaseExecutor
    body_recovery_observer: RegisteredRecoveryObserver
    state_recovery_observer: RegisteredRecoveryObserver
    organ_recovery_observer: RegisteredRecoveryObserver
    independent_release_verifier: IndependentProtectionReleaseVerifier

    def run_time_cycle(self) -> CycleOutcome:
        if self.protection_boundary.mode is ProtectionMode.PROTECTED:
            raise ProtectionActiveError(
                "normal life cycle is blocked while protection is active"
            )
        return Stage7System.run_time_cycle(self)

    def receive_text(self, raw: RawTextInput) -> Stage6InputOutcome:
        if self.protection_boundary.mode is ProtectionMode.PROTECTED:
            return self.text_input_runner.run(raw)
        return Stage7System.receive_text(self, raw)

    def activate_fixed_protection(
        self,
        observed_at: ExternalTime,
    ) -> FixedProtectionResult:
        observation = self.fixed_observer.observe_normal_authority_unavailable(
            observed_at
        )
        reservation_id = self.fixed_counter.reserve(observation)
        if reservation_id is None:
            raise ProtectionActiveError(
                "fixed protection execution count could not be reserved"
            )
        request = self.fixed_request_factory.make(observation, reservation_id)
        permit = self.protection_boundary.authorize_fixed_inward_operation(request)
        if permit is None:
            raise ProtectionActiveError(
                "fixed protection request did not pass the fixed definition"
            )
        return self.fixed_executor.execute(permit)

    def request_external_repair(self, request: ExternalRepairRequest) -> bool:
        return self.protection_boundary.reject_external_repair_as_inward(request)

    def release_protection(self, request: ProtectionReleaseRequest) -> bool:
        permit = self.protection_boundary.authorize_release(request)
        if permit is None:
            return False
        self.release_executor.execute(permit)
        return True


def make_stage8_system(
    *,
    persistence_path: str | Path = Path(".yamicha/yamicha.sqlite3"),
    require_existing_persistence: bool = False,
    clock: Clock | None = None,
    runtime_id_factory: Callable[[], str] | None = None,
    time_correlation_id_factory: Callable[[], str] | None = None,
    input_correlation_id_factory: Callable[[], str] | None = None,
    reception_id_factory: Callable[[], str] | None = None,
    event_id_factory: Callable[[], str] | None = None,
    request_id_factory: Callable[[], str] | None = None,
    expression_request_id_factory: Callable[[], str] | None = None,
    expression_artifact_id_factory: Callable[[], str] | None = None,
    lifecycle_record_id_factory: Callable[[], str] | None = None,
    record_entry_id_factory: Callable[[], str] | None = None,
    retention_candidate_id_factory: Callable[[], str] | None = None,
    candidate_review_id_factory: Callable[[], str] | None = None,
    memory_item_id_factory: Callable[[], str] | None = None,
    subject_id_factory: Callable[[], str] | None = None,
    session_id_factory: Callable[[], str] | None = None,
    snapshot_id_factory: Callable[[], str] | None = None,
    persistence_time_factory: Callable[[], ExternalTime] | None = None,
    audit_id_factory: Callable[[], str] | None = None,
    permit_id_factory: Callable[[], str] | None = None,
    observation_id_factory: Callable[[], str] | None = None,
    reservation_id_factory: Callable[[], str] | None = None,
    fixed_request_id_factory: Callable[[], str] | None = None,
    activation_id_factory: Callable[[], str] | None = None,
    recovery_observation_id_factory: Callable[[], str] | None = None,
    release_evaluation_id_factory: Callable[[], str] | None = None,
    release_finalization_id_factory: Callable[[], str] | None = None,
    release_proposal_id_factory: Callable[[], str] | None = None,
    release_verification_id_factory: Callable[[], str] | None = None,
    memory_available: bool = True,
    known_counterpart_id: str = "human-001",
    authorized_input_sources: tuple[str, ...] | None = None,
    normal_dialogue_output_enabled: bool = True,
    _configuration_version: str = CONFIGURATION_VERSION,
    _upgrade_from_configuration_versions: tuple[str, ...] = ("stage7-v1",),
    _sensation_factory: Callable[..., Stage3Sensation] = Stage3Sensation,
    _core_factory: Callable[..., Stage8Core] = Stage8Core,
    _judgment_factory: Callable[..., Stage8Judgment] = Stage8Judgment,
    _language_factory: Callable[..., Stage5Language] = Stage5Language,
    _sensation_options: dict[str, object] | None = None,
    _core_options: dict[str, object] | None = None,
    _judgment_options: dict[str, object] | None = None,
    _language_options: dict[str, object] | None = None,
) -> Stage8System:
    now_factory = persistence_time_factory or (
        lambda: ExternalTime(datetime.now(UTC))
    )
    runtime = Stage2Runtime(clock=clock, id_factory=runtime_id_factory)
    sensation = _sensation_factory(
        reception_id_factory=reception_id_factory,
        event_id_factory=event_id_factory,
        **(_sensation_options or {}),
    )
    state = Stage7State()
    memory = Stage7Memory(
        available=memory_available,
        review_id_factory=candidate_review_id_factory,
        memory_item_id_factory=memory_item_id_factory,
    )
    relationship = Stage7Relationship(known_counterpart_id=known_counterpart_id)
    core = _core_factory(
        state=state,
        memory=memory,
        relationship=relationship,
        request_id_factory=request_id_factory,
        expression_request_id_factory=expression_request_id_factory,
        lifecycle_record_id_factory=lifecycle_record_id_factory,
        record_entry_id_factory=record_entry_id_factory,
        release_finalization_id_factory=release_finalization_id_factory,
        release_proposal_id_factory=release_proposal_id_factory,
        **(_core_options or {}),
    )
    judgment = _judgment_factory(
        candidate_id_factory=retention_candidate_id_factory,
        release_evaluation_id_factory=release_evaluation_id_factory,
        **(_judgment_options or {}),
    )
    language = _language_factory(
        artifact_id_factory=expression_artifact_id_factory,
        **(_language_options or {}),
    )
    store, recovery = SQLitePersistenceStore.open(
        persistence_path,
        configuration_version=_configuration_version,
        subject_id_factory=subject_id_factory,
        session_id_factory=session_id_factory,
        now_factory=now_factory,
        require_existing=require_existing_persistence,
        upgrade_from_configuration_versions=(
            _upgrade_from_configuration_versions
        ),
    )
    try:
        store.initialize_protection_storage(
            definition_version=PROTECTION_DEFINITION_VERSION,
            initialized_at=now_factory(),
        )
        store.protection_control_state()
        store.protection_audit_records()
    except Exception:
        store.close()
        raise
    body_recovery_observer = RegisteredRecoveryObserver(
        RecoveryEvidenceSource.BODY,
        observation_id_factory=recovery_observation_id_factory,
    )
    state_recovery_observer = RegisteredRecoveryObserver(
        RecoveryEvidenceSource.STATE,
        observation_id_factory=recovery_observation_id_factory,
    )
    organ_recovery_observer = RegisteredRecoveryObserver(
        RecoveryEvidenceSource.AFFECTED_ORGAN,
        observation_id_factory=recovery_observation_id_factory,
    )
    independent_release_verifier = IndependentProtectionReleaseVerifier(
        store,
        verification_id_factory=release_verification_id_factory,
    )

    def release_proposal_was_issued(
        proposal: ProtectionReleaseProposal,
        observations: tuple[RecoveryObservation, ...],
    ) -> bool:
        issued_proposal = core.issued_release_proposal(proposal.proposal_id)
        evaluation = judgment.issued_release_evaluation(
            proposal.judgment_approval_id
        )
        return (
            issued_proposal == proposal
            and evaluation is not None
            and evaluation.activation_id == proposal.activation_id
            and evaluation.protection_definition_version
            == proposal.protection_definition_version
            and evaluation.observation_ids
            == tuple(observation.observation_id for observation in observations)
        )

    protection_boundary = Stage8ProtectionBoundary(
        store=store,
        authorized_input_sources=(
            authorized_input_sources
            if authorized_input_sources is not None
            else (known_counterpart_id,)
        ),
        normal_dialogue_output_enabled=normal_dialogue_output_enabled,
        audit_id_factory=audit_id_factory,
        permit_id_factory=permit_id_factory,
        audit_time_factory=now_factory,
        recovery_observation_validators={
            RecoveryEvidenceSource.BODY: body_recovery_observer.issued,
            RecoveryEvidenceSource.STATE: state_recovery_observer.issued,
            RecoveryEvidenceSource.AFFECTED_ORGAN: organ_recovery_observer.issued,
        },
        release_proposal_validator=release_proposal_was_issued,
        independent_verification_validator=(
            independent_release_verifier.issued
        ),
    )
    composition = YamichaComposition(
        core=core,
        memory=memory,
        state=state,
        sensation=sensation,
        judgment=judgment,
        relationship=relationship,
        capability=CapabilityStub(),
        language=language,
        auxiliary_intelligence=AuxiliaryIntelligenceStub(),
        runtime=runtime,
        protection_boundary=protection_boundary,
        external_effect_gate=ExternalEffectGateStub(),
    )
    time_runner = InputFreeCycleRunner(
        sensation=sensation,
        state=state,
        judgment=judgment,
        core=core,
        runtime=runtime,
        correlation_id_factory=time_correlation_id_factory or (lambda: str(uuid4())),
    )
    try:
        if recovery.snapshot is not None:
            snapshot = recovery.snapshot
            state.restore_owned_state(snapshot.state)
            core.restore_lifecycle_records(snapshot.lifecycle_records)
            memory.restore_owned_information(snapshot.memory)
            relationship.restore_owned_state(snapshot.relationship)
            protection_boundary.restore_owned_state(snapshot.protection)
    except (RuntimeError, ValueError) as error:
        store.close()
        raise PersistenceConsistencyError(
            "stored checkpoint cannot be restored by its information owners"
        ) from error
    coordinator = Stage8SnapshotCoordinator(
        store=store,
        recovery=recovery,
        state=state,
        core=core,
        memory=memory,
        relationship=relationship,
        protection_boundary=protection_boundary,
        snapshot_id_factory=snapshot_id_factory,
    )
    text_runner = Stage8TextInputRunner(
        protection_boundary=protection_boundary,
        sensation=sensation,
        core=core,
        judgment=judgment,
        language=language,
        correlation_id_factory=input_correlation_id_factory
        or (lambda: str(uuid4())),
        snapshot_coordinator=coordinator,
    )
    return Stage8System(
        composition=composition,
        runtime=runtime,
        state=state,
        memory=memory,
        sensation=sensation,
        judgment=judgment,
        language=language,
        core=core,
        relationship=relationship,
        protection_boundary=protection_boundary,
        persistence=store,
        recovery=recovery,
        time_cycle_runner=time_runner,
        snapshot_coordinator=coordinator,
        text_input_runner=text_runner,
        shutdown_time_factory=now_factory,
        fixed_observer=FixedProtectionObserver(
            observation_id_factory=observation_id_factory
        ),
        fixed_counter=FixedProtectionCounter(
            store,
            reservation_id_factory=reservation_id_factory,
        ),
        fixed_request_factory=FixedProtectionRequestFactory(
            request_id_factory=fixed_request_id_factory
        ),
        fixed_executor=FixedInwardProtectionExecutor(
            store,
            activation_id_factory=activation_id_factory,
            audit_id_factory=audit_id_factory,
        ),
        release_executor=ProtectionReleaseExecutor(
            store,
            audit_id_factory=audit_id_factory,
        ),
        body_recovery_observer=body_recovery_observer,
        state_recovery_observer=state_recovery_observer,
        organ_recovery_observer=organ_recovery_observer,
        independent_release_verifier=independent_release_verifier,
    )
