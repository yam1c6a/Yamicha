"""Compose stage-7 transactional persistence and restoration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar
from uuid import uuid4

from yamicha.body.external_effect_gate import ExternalEffectGateStub
from yamicha.body.persistence import PersistenceConsistencyError, SQLitePersistenceStore
from yamicha.body.protection_boundary import Stage7ProtectionBoundary
from yamicha.body.runtime import Clock, RuntimeStatus, Stage2Runtime
from yamicha.contracts import (
    CycleOutcome,
    ExecutionOpportunity,
    ExternalTime,
    InputDisposition,
    PersistenceOpenResult,
    PersistenceSnapshot,
    RawTextInput,
    Stage6InputOutcome,
)
from yamicha.life.stage3 import Stage3Sensation
from yamicha.life.stage5 import Stage5Language
from yamicha.life.stage6 import Stage6Judgment
from yamicha.life.stage7 import (
    Stage7Core,
    Stage7Memory,
    Stage7Relationship,
    Stage7State,
)
from yamicha.life.stubs import AuxiliaryIntelligenceStub, CapabilityStub

from .composition import YamichaComposition
from .stage2 import InputFreeCycleRunner
from .stage6 import Stage6TextInputRunner


CONFIGURATION_VERSION = "stage7-v1"


class Stage7SnapshotCoordinator:
    def __init__(
        self,
        *,
        store: SQLitePersistenceStore,
        recovery: PersistenceOpenResult,
        state: Stage7State,
        core: Stage7Core,
        memory: Stage7Memory,
        relationship: Stage7Relationship,
        protection_boundary: Stage7ProtectionBoundary,
        snapshot_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._store = store
        self._recovery = recovery
        self._state = state
        self._core = core
        self._memory = memory
        self._relationship = relationship
        self._protection_boundary = protection_boundary
        self._snapshot_id_factory = snapshot_id_factory or (lambda: str(uuid4()))

    def commit(self, created_at: ExternalTime) -> PersistenceSnapshot:
        snapshot_id = self._snapshot_id_factory()
        if not snapshot_id.strip():
            raise ValueError("snapshot ID factory returned an empty identifier")
        identity = self._recovery.identity
        snapshot = PersistenceSnapshot(
            snapshot_id=snapshot_id,
            sequence=self._store.latest_sequence + 1,
            created_at=created_at,
            subject_id=identity.subject_id,
            configuration_version=identity.configuration_version,
            state=self._state.persistence_snapshot(),
            lifecycle_records=self._core.lifecycle_records,
            memory=self._memory.persistence_snapshot(),
            relationship=self._relationship.persistence_snapshot(),
            protection=self._protection_boundary.persistence_snapshot(),
        )
        self._store.commit_snapshot(snapshot)
        return snapshot


class Stage7TextInputRunner(Stage6TextInputRunner):
    def __init__(
        self,
        *,
        protection_boundary: Stage7ProtectionBoundary,
        sensation: Stage3Sensation,
        core: Stage7Core,
        judgment: Stage6Judgment,
        language: Stage5Language,
        correlation_id_factory: Callable[[], str],
        snapshot_coordinator: Stage7SnapshotCoordinator,
    ) -> None:
        super().__init__(
            protection_boundary=protection_boundary,
            sensation=sensation,
            core=core,
            judgment=judgment,
            language=language,
            correlation_id_factory=correlation_id_factory,
        )
        self._snapshot_coordinator = snapshot_coordinator
        self._persistence_healthy = True

    @property
    def persistence_healthy(self) -> bool:
        return self._persistence_healthy

    def mark_persistence_unhealthy(self) -> None:
        self._persistence_healthy = False

    def run(self, raw: RawTextInput) -> Stage6InputOutcome:
        if not self._persistence_healthy:
            raise RuntimeError("persistence is unavailable after a failed checkpoint")
        outcome = super().run(raw)
        if outcome.disposition is InputDisposition.ACCEPTED:
            try:
                self._snapshot_coordinator.commit(raw.received_at)
            except Exception:
                self._persistence_healthy = False
                raise
        return outcome


@dataclass(slots=True)
class Stage7System:
    stage_label: ClassVar[int] = 7
    composition: YamichaComposition
    runtime: Stage2Runtime
    state: Stage7State
    memory: Stage7Memory
    sensation: Stage3Sensation
    judgment: Stage6Judgment
    language: Stage5Language
    core: Stage7Core
    relationship: Stage7Relationship
    protection_boundary: Stage7ProtectionBoundary
    persistence: SQLitePersistenceStore
    recovery: PersistenceOpenResult
    time_cycle_runner: InputFreeCycleRunner
    snapshot_coordinator: Stage7SnapshotCoordinator
    text_input_runner: Stage7TextInputRunner
    shutdown_time_factory: Callable[[], ExternalTime]
    _startup_opportunity: ExecutionOpportunity | None = None
    _closed: bool = False

    def start(self) -> None:
        if self._closed:
            raise RuntimeError("stage-7 system is closed")
        if self._startup_opportunity is not None:
            raise RuntimeError("stage-7 system already has a startup opportunity")
        self._startup_opportunity = self.runtime.start()

    def run_time_cycle(self) -> CycleOutcome:
        if self._closed:
            raise RuntimeError("stage-7 system is closed")
        if self.runtime.status is RuntimeStatus.NOT_STARTED:
            self.start()
        if self._startup_opportunity is not None:
            opportunity = self._startup_opportunity
            self._startup_opportunity = None
        else:
            opportunity = self.runtime.periodic_opportunity()
        outcome = self.time_cycle_runner.run(opportunity)
        try:
            self.snapshot_coordinator.commit(opportunity.external_time)
        except Exception:
            self.text_input_runner.mark_persistence_unhealthy()
            raise
        return outcome

    def receive_text(self, raw: RawTextInput) -> Stage6InputOutcome:
        if self._closed:
            raise RuntimeError("stage-7 system is closed")
        if self.runtime.status is RuntimeStatus.NOT_STARTED or (
            self._startup_opportunity is not None
        ):
            self.run_time_cycle()
        if self.runtime.status is not RuntimeStatus.WAITING:
            raise RuntimeError(
                f"text input requires waiting runtime: {self.runtime.status}"
            )
        return self.text_input_runner.run(raw)

    def stop(self) -> None:
        self.shutdown()

    def shutdown(self) -> None:
        if self._closed:
            return
        try:
            if self.runtime.status not in {
                RuntimeStatus.NOT_STARTED,
                RuntimeStatus.STOPPED,
            }:
                self.state.stop()
                self.runtime.stop()
                self._startup_opportunity = None
            if self.text_input_runner.persistence_healthy:
                self.persistence.mark_normal_shutdown(
                    self.recovery.session_id,
                    self.shutdown_time_factory(),
                )
        finally:
            self.persistence.close()
            self._closed = True

    def abandon(self) -> None:
        """Close without a normal marker, equivalent to an interrupted process."""
        if not self._closed:
            self.persistence.close()
            self._closed = True


def make_stage7_system(
    *,
    persistence_path: str | Path = Path(".yamicha/yamicha.sqlite3"),
    require_existing_persistence: bool = False,
    configuration_version: str = CONFIGURATION_VERSION,
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
    memory_available: bool = True,
    known_counterpart_id: str = "human-001",
    normal_dialogue_output_enabled: bool = True,
) -> Stage7System:
    now_factory = persistence_time_factory or (
        lambda: ExternalTime(datetime.now(UTC))
    )
    runtime = Stage2Runtime(clock=clock, id_factory=runtime_id_factory)
    protection_boundary = Stage7ProtectionBoundary(
        normal_dialogue_output_enabled=normal_dialogue_output_enabled,
    )
    sensation = Stage3Sensation(
        reception_id_factory=reception_id_factory,
        event_id_factory=event_id_factory,
    )
    state = Stage7State()
    memory = Stage7Memory(
        available=memory_available,
        review_id_factory=candidate_review_id_factory,
        memory_item_id_factory=memory_item_id_factory,
    )
    relationship = Stage7Relationship(known_counterpart_id=known_counterpart_id)
    core = Stage7Core(
        state=state,
        memory=memory,
        relationship=relationship,
        request_id_factory=request_id_factory,
        expression_request_id_factory=expression_request_id_factory,
        lifecycle_record_id_factory=lifecycle_record_id_factory,
        record_entry_id_factory=record_entry_id_factory,
    )
    judgment = Stage6Judgment(candidate_id_factory=retention_candidate_id_factory)
    language = Stage5Language(artifact_id_factory=expression_artifact_id_factory)
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
    store, recovery = SQLitePersistenceStore.open(
        persistence_path,
        configuration_version=configuration_version,
        subject_id_factory=subject_id_factory,
        session_id_factory=session_id_factory,
        now_factory=now_factory,
        require_existing=require_existing_persistence,
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
    coordinator = Stage7SnapshotCoordinator(
        store=store,
        recovery=recovery,
        state=state,
        core=core,
        memory=memory,
        relationship=relationship,
        protection_boundary=protection_boundary,
        snapshot_id_factory=snapshot_id_factory,
    )
    text_runner = Stage7TextInputRunner(
        protection_boundary=protection_boundary,
        sensation=sensation,
        core=core,
        judgment=judgment,
        language=language,
        correlation_id_factory=input_correlation_id_factory
        or (lambda: str(uuid4())),
        snapshot_coordinator=coordinator,
    )
    return Stage7System(
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
    )
