"""Compose and run stage-4 minimal judgment after protected text input."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from yamicha.body.external_effect_gate import ExternalEffectGateStub
from yamicha.body.protection_boundary import Stage4ProtectionBoundary
from yamicha.body.runtime import Clock, RuntimeStatus, Stage2Runtime
from yamicha.contracts import (
    CycleOutcome,
    ExecutionOpportunity,
    InputDisposition,
    InputQuality,
    InputRejection,
    RawTextInput,
    Stage4InputOutcome,
)
from yamicha.life.stage3 import Stage3Sensation
from yamicha.life.stage4 import (
    Stage4Core,
    Stage4Judgment,
    Stage4Memory,
    Stage4Relationship,
    Stage4State,
)
from yamicha.life.stubs import AuxiliaryIntelligenceStub, CapabilityStub, LanguageStub

from .composition import YamichaComposition
from .stage2 import InputFreeCycleRunner


class Stage4TextInputRunner:
    """Run protected input through routing, materials, judgment, and Core."""

    def __init__(
        self,
        *,
        protection_boundary: Stage4ProtectionBoundary,
        sensation: Stage3Sensation,
        core: Stage4Core,
        judgment: Stage4Judgment,
        correlation_id_factory: Callable[[], str],
    ) -> None:
        self._protection_boundary = protection_boundary
        self._sensation = sensation
        self._core = core
        self._judgment = judgment
        self._correlation_id_factory = correlation_id_factory
        self._used_correlation_ids: set[str] = set()

    def run(self, raw: RawTextInput) -> Stage4InputOutcome:
        correlation_id = self._correlation_id_factory()
        if not correlation_id.strip() or correlation_id in self._used_correlation_ids:
            raise ValueError("input correlation ID must be non-empty and unique")
        self._used_correlation_ids.add(correlation_id)

        validation = self._protection_boundary.validate(raw)
        if isinstance(validation, InputRejection):
            return Stage4InputOutcome(
                correlation_id=correlation_id,
                disposition=validation.disposition,
                rejection=validation,
            )

        event = self._sensation.receive_validated_text(validation, correlation_id)
        cycle = self._core.accept_event(event)
        if event.quality is InputQuality.DUPLICATE:
            return Stage4InputOutcome(
                correlation_id=correlation_id,
                disposition=InputDisposition.DUPLICATE,
                cycle=cycle,
            )

        boundary = self._protection_boundary.present_decision_material(event)
        context = self._core.build_judgment_context(cycle, boundary)
        result = self._judgment.evaluate(context)
        finalization = self._core.finalize_judgment(result, context)
        return Stage4InputOutcome(
            correlation_id=correlation_id,
            disposition=InputDisposition.ACCEPTED,
            cycle=cycle,
            context=context,
            judgment=result,
            finalization=finalization,
        )


@dataclass(slots=True)
class Stage4System:
    composition: YamichaComposition
    runtime: Stage2Runtime
    state: Stage4State
    sensation: Stage3Sensation
    judgment: Stage4Judgment
    core: Stage4Core
    protection_boundary: Stage4ProtectionBoundary
    time_cycle_runner: InputFreeCycleRunner
    text_input_runner: Stage4TextInputRunner
    _startup_opportunity: ExecutionOpportunity | None = None

    def start(self) -> None:
        if self._startup_opportunity is not None:
            raise RuntimeError("stage-4 system already has a startup opportunity")
        self._startup_opportunity = self.runtime.start()

    def run_time_cycle(self) -> CycleOutcome:
        if self.runtime.status is RuntimeStatus.NOT_STARTED:
            self.start()
        if self._startup_opportunity is not None:
            opportunity = self._startup_opportunity
            self._startup_opportunity = None
        else:
            opportunity = self.runtime.periodic_opportunity()
        return self.time_cycle_runner.run(opportunity)

    def receive_text(self, raw: RawTextInput) -> Stage4InputOutcome:
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
        if self.runtime.status in {RuntimeStatus.NOT_STARTED, RuntimeStatus.STOPPED}:
            raise RuntimeError(f"cannot stop stage-4 system from {self.runtime.status}")
        self.state.stop()
        self.runtime.stop()
        self._startup_opportunity = None


def make_stage4_system(
    *,
    clock: Clock | None = None,
    runtime_id_factory: Callable[[], str] | None = None,
    time_correlation_id_factory: Callable[[], str] | None = None,
    input_correlation_id_factory: Callable[[], str] | None = None,
    reception_id_factory: Callable[[], str] | None = None,
    event_id_factory: Callable[[], str] | None = None,
    request_id_factory: Callable[[], str] | None = None,
    memory_available: bool = True,
    known_counterpart_id: str = "human-001",
) -> Stage4System:
    runtime = Stage2Runtime(clock=clock, id_factory=runtime_id_factory)
    protection_boundary = Stage4ProtectionBoundary()
    sensation = Stage3Sensation(
        reception_id_factory=reception_id_factory,
        event_id_factory=event_id_factory,
    )
    state = Stage4State()
    memory = Stage4Memory(available=memory_available)
    relationship = Stage4Relationship(
        known_counterpart_id=known_counterpart_id,
    )
    core = Stage4Core(
        state=state,
        memory=memory,
        relationship=relationship,
        request_id_factory=request_id_factory,
    )
    judgment = Stage4Judgment()
    composition = YamichaComposition(
        core=core,
        memory=memory,
        state=state,
        sensation=sensation,
        judgment=judgment,
        relationship=relationship,
        capability=CapabilityStub(),
        language=LanguageStub(),
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
        correlation_id_factory=(
            time_correlation_id_factory or (lambda: str(uuid4()))
        ),
    )
    text_runner = Stage4TextInputRunner(
        protection_boundary=protection_boundary,
        sensation=sensation,
        core=core,
        judgment=judgment,
        correlation_id_factory=(
            input_correlation_id_factory or (lambda: str(uuid4()))
        ),
    )
    return Stage4System(
        composition=composition,
        runtime=runtime,
        state=state,
        sensation=sensation,
        judgment=judgment,
        core=core,
        protection_boundary=protection_boundary,
        time_cycle_runner=time_runner,
        text_input_runner=text_runner,
    )
