"""Compose and run the minimal input-free stage-2 lifecycle."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from yamicha.body.external_effect_gate import ExternalEffectGateStub
from yamicha.body.protection_boundary import ProtectionBoundaryStub
from yamicha.body.runtime import Clock, RuntimeStatus, Stage2Runtime
from yamicha.contracts import CycleOutcome, CycleStatus, ExecutionOpportunity
from yamicha.life.stage2 import (
    Stage2Core,
    Stage2Judgment,
    Stage2Sensation,
    Stage2State,
)
from yamicha.life.stubs import (
    AuxiliaryIntelligenceStub,
    CapabilityStub,
    LanguageStub,
    MemoryStub,
    RelationshipStub,
)

from .composition import YamichaComposition


class InputFreeCycleRunner:
    """Physical call sequence; all choices remain with the responsible organs."""

    def __init__(
        self,
        *,
        sensation: Stage2Sensation,
        state: Stage2State,
        judgment: Stage2Judgment,
        core: Stage2Core,
        runtime: Stage2Runtime,
        correlation_id_factory: Callable[[], str],
    ) -> None:
        self._sensation = sensation
        self._state = state
        self._judgment = judgment
        self._core = core
        self._runtime = runtime
        self._correlation_id_factory = correlation_id_factory

    def run(self, opportunity: ExecutionOpportunity) -> CycleOutcome:
        correlation_id = self._correlation_id_factory()
        if not correlation_id.strip():
            raise ValueError("correlation_id_factory returned an empty identifier")
        event = self._sensation.receive_execution_opportunity(
            opportunity,
            correlation_id,
        )
        observed_state = self._state.observe_internal_event(event)
        proposal = self._judgment.propose_for_state(observed_state)
        decision = self._core.finalize(proposal)
        final_state = self._state.enter_waiting(decision)
        self._runtime.wait()
        return CycleOutcome(
            status=CycleStatus.COMPLETED,
            opportunity=opportunity,
            observed_state=observed_state,
            proposal=proposal,
            decision=decision,
            final_state=final_state,
        )


@dataclass(slots=True)
class Stage2System:
    """A complete stage-2 composition plus its technical cycle runner."""

    composition: YamichaComposition
    runtime: Stage2Runtime
    state: Stage2State
    cycle_runner: InputFreeCycleRunner
    _startup_opportunity: ExecutionOpportunity | None = None

    def start(self) -> None:
        if self._startup_opportunity is not None:
            raise RuntimeError("stage-2 system already has a startup opportunity")
        self._startup_opportunity = self.runtime.start()

    def run_cycle(self) -> CycleOutcome:
        if self.runtime.status is RuntimeStatus.NOT_STARTED:
            self.start()
        if self._startup_opportunity is not None:
            opportunity = self._startup_opportunity
            self._startup_opportunity = None
        else:
            opportunity = self.runtime.periodic_opportunity()
        return self.cycle_runner.run(opportunity)

    def stop(self) -> None:
        if self.runtime.status in {
            RuntimeStatus.NOT_STARTED,
            RuntimeStatus.STOPPED,
        }:
            raise RuntimeError(f"cannot stop stage-2 system from {self.runtime.status}")
        self.state.stop()
        self.runtime.stop()
        self._startup_opportunity = None


def make_stage2_system(
    *,
    clock: Clock | None = None,
    id_factory: Callable[[], str] | None = None,
    correlation_id_factory: Callable[[], str] | None = None,
) -> Stage2System:
    """Create all stage-2 responsibilities and their sole composition route."""

    runtime = Stage2Runtime(clock=clock, id_factory=id_factory)
    sensation = Stage2Sensation()
    state = Stage2State()
    judgment = Stage2Judgment()
    core = Stage2Core()
    composition = YamichaComposition(
        core=core,
        memory=MemoryStub(),
        state=state,
        sensation=sensation,
        judgment=judgment,
        relationship=RelationshipStub(),
        capability=CapabilityStub(),
        language=LanguageStub(),
        auxiliary_intelligence=AuxiliaryIntelligenceStub(),
        runtime=runtime,
        protection_boundary=ProtectionBoundaryStub(),
        external_effect_gate=ExternalEffectGateStub(),
    )
    runner = InputFreeCycleRunner(
        sensation=sensation,
        state=state,
        judgment=judgment,
        core=core,
        runtime=runtime,
        correlation_id_factory=(
            correlation_id_factory or (lambda: str(uuid4()))
        ),
    )
    return Stage2System(
        composition=composition,
        runtime=runtime,
        state=state,
        cycle_runner=runner,
    )
