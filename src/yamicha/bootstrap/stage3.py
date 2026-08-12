"""Compose and run stage-3 text input through the protected Core route."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from yamicha.body.external_effect_gate import ExternalEffectGateStub
from yamicha.body.protection_boundary import Stage3ProtectionBoundary
from yamicha.body.runtime import Clock, RuntimeStatus, Stage2Runtime
from yamicha.contracts import (
    CycleOutcome,
    ExecutionOpportunity,
    InputDisposition,
    InputProcessingOutcome,
    InputQuality,
    InputRejection,
    RawTextInput,
)
from yamicha.life.stage2 import Stage2Judgment
from yamicha.life.stage3 import (
    Stage3Core,
    Stage3Memory,
    Stage3Relationship,
    Stage3Sensation,
    Stage3State,
)
from yamicha.life.stubs import (
    AuxiliaryIntelligenceStub,
    CapabilityStub,
    LanguageStub,
)

from .composition import YamichaComposition
from .stage2 import InputFreeCycleRunner


class TextInputRunner:
    """Physical boundary sequence: protection, Sensation, then Core only."""

    def __init__(
        self,
        *,
        protection_boundary: Stage3ProtectionBoundary,
        sensation: Stage3Sensation,
        core: Stage3Core,
        correlation_id_factory: Callable[[], str],
    ) -> None:
        self._protection_boundary = protection_boundary
        self._sensation = sensation
        self._core = core
        self._correlation_id_factory = correlation_id_factory
        self._used_correlation_ids: set[str] = set()

    def run(self, raw: RawTextInput) -> InputProcessingOutcome:
        correlation_id = self._correlation_id_factory()
        if (
            not correlation_id.strip()
            or correlation_id in self._used_correlation_ids
        ):
            raise ValueError("input correlation ID must be non-empty and unique")
        self._used_correlation_ids.add(correlation_id)
        validation = self._protection_boundary.validate(raw)
        if isinstance(validation, InputRejection):
            return InputProcessingOutcome(
                correlation_id=correlation_id,
                disposition=validation.disposition,
                rejection=validation,
            )
        event = self._sensation.receive_validated_text(validation, correlation_id)
        cycle = self._core.accept_event(event)
        disposition = (
            InputDisposition.DUPLICATE
            if event.quality is InputQuality.DUPLICATE
            else InputDisposition.ACCEPTED
        )
        return InputProcessingOutcome(
            correlation_id=correlation_id,
            disposition=disposition,
            cycle=cycle,
        )


@dataclass(slots=True)
class Stage3System:
    composition: YamichaComposition
    runtime: Stage2Runtime
    state: Stage3State
    sensation: Stage3Sensation
    core: Stage3Core
    protection_boundary: Stage3ProtectionBoundary
    time_cycle_runner: InputFreeCycleRunner
    text_input_runner: TextInputRunner
    _startup_opportunity: ExecutionOpportunity | None = None

    def start(self) -> None:
        if self._startup_opportunity is not None:
            raise RuntimeError("stage-3 system already has a startup opportunity")
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

    def receive_text(self, raw: RawTextInput) -> InputProcessingOutcome:
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
        if self.runtime.status in {
            RuntimeStatus.NOT_STARTED,
            RuntimeStatus.STOPPED,
        }:
            raise RuntimeError(f"cannot stop stage-3 system from {self.runtime.status}")
        self.state.stop()
        self.runtime.stop()
        self._startup_opportunity = None


def make_stage3_system(
    *,
    clock: Clock | None = None,
    runtime_id_factory: Callable[[], str] | None = None,
    time_correlation_id_factory: Callable[[], str] | None = None,
    input_correlation_id_factory: Callable[[], str] | None = None,
    reception_id_factory: Callable[[], str] | None = None,
    event_id_factory: Callable[[], str] | None = None,
    request_id_factory: Callable[[], str] | None = None,
) -> Stage3System:
    runtime = Stage2Runtime(clock=clock, id_factory=runtime_id_factory)
    protection_boundary = Stage3ProtectionBoundary()
    sensation = Stage3Sensation(
        reception_id_factory=reception_id_factory,
        event_id_factory=event_id_factory,
    )
    state = Stage3State()
    memory = Stage3Memory()
    relationship = Stage3Relationship()
    core = Stage3Core(
        {
            state.definition.identifier: state,
            memory.definition.identifier: memory,
            relationship.definition.identifier: relationship,
        },
        request_id_factory=request_id_factory,
    )
    judgment = Stage2Judgment()
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
    text_runner = TextInputRunner(
        protection_boundary=protection_boundary,
        sensation=sensation,
        core=core,
        correlation_id_factory=(
            input_correlation_id_factory or (lambda: str(uuid4()))
        ),
    )
    return Stage3System(
        composition=composition,
        runtime=runtime,
        state=state,
        sensation=sensation,
        core=core,
        protection_boundary=protection_boundary,
        time_cycle_runner=time_runner,
        text_input_runner=text_runner,
    )
