"""Compose stage-5 judgment, expression, review, and dialogue output."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from yamicha.body.external_effect_gate import ExternalEffectGateStub
from yamicha.body.protection_boundary import Stage5ProtectionBoundary
from yamicha.body.runtime import Clock, RuntimeStatus, Stage2Runtime
from yamicha.contracts import (
    CycleOutcome,
    ExecutionOpportunity,
    FinalizationStatus,
    InputDisposition,
    InputQuality,
    InputRejection,
    RawTextInput,
    Stage5InputOutcome,
)
from yamicha.life.stage3 import Stage3Sensation
from yamicha.life.stage4 import (
    Stage4Judgment,
    Stage4Memory,
    Stage4Relationship,
    Stage4State,
)
from yamicha.life.stage5 import Stage5Core, Stage5Language
from yamicha.life.stubs import AuxiliaryIntelligenceStub, CapabilityStub

from .composition import YamichaComposition
from .stage2 import InputFreeCycleRunner


class Stage5TextInputRunner:
    def __init__(
        self,
        *,
        protection_boundary: Stage5ProtectionBoundary,
        sensation: Stage3Sensation,
        core: Stage5Core,
        judgment: Stage4Judgment,
        language: Stage5Language,
        correlation_id_factory: Callable[[], str],
    ) -> None:
        self._protection_boundary = protection_boundary
        self._sensation = sensation
        self._core = core
        self._judgment = judgment
        self._language = language
        self._correlation_id_factory = correlation_id_factory
        self._used_correlation_ids: set[str] = set()

    def run(self, raw: RawTextInput) -> Stage5InputOutcome:
        correlation_id = self._correlation_id_factory()
        if not correlation_id.strip() or correlation_id in self._used_correlation_ids:
            raise ValueError("input correlation ID must be non-empty and unique")
        self._used_correlation_ids.add(correlation_id)

        validation = self._protection_boundary.validate(raw)
        if isinstance(validation, InputRejection):
            return Stage5InputOutcome(
                correlation_id=correlation_id,
                disposition=validation.disposition,
                rejection=validation,
            )

        event = self._sensation.receive_validated_text(validation, correlation_id)
        cycle = self._core.accept_event(event)
        if event.quality is InputQuality.DUPLICATE:
            return Stage5InputOutcome(
                correlation_id=correlation_id,
                disposition=InputDisposition.DUPLICATE,
                cycle=cycle,
            )

        boundary = self._protection_boundary.present_decision_material(event)
        context = self._core.build_judgment_context(cycle, boundary)
        judgment = self._judgment.evaluate(context)
        finalization = self._core.finalize_judgment(judgment, context)
        if finalization.status is not FinalizationStatus.FINALIZED:
            return Stage5InputOutcome(
                correlation_id=correlation_id,
                disposition=InputDisposition.ACCEPTED,
                cycle=cycle,
                context=context,
                judgment=judgment,
                finalization=finalization,
            )

        expression_request = self._core.make_expression_request(
            finalization,
            judgment,
            context,
        )
        expression = self._language.express(expression_request)
        expression_review = self._core.review_expression(
            expression_request,
            expression,
        )
        dialogue_output = self._protection_boundary.release_dialogue_output(
            expression,
            expression_review,
        )
        return Stage5InputOutcome(
            correlation_id=correlation_id,
            disposition=InputDisposition.ACCEPTED,
            cycle=cycle,
            context=context,
            judgment=judgment,
            finalization=finalization,
            expression_request=expression_request,
            expression=expression,
            expression_review=expression_review,
            dialogue_output=dialogue_output,
        )


@dataclass(slots=True)
class Stage5System:
    composition: YamichaComposition
    runtime: Stage2Runtime
    state: Stage4State
    sensation: Stage3Sensation
    judgment: Stage4Judgment
    language: Stage5Language
    core: Stage5Core
    protection_boundary: Stage5ProtectionBoundary
    time_cycle_runner: InputFreeCycleRunner
    text_input_runner: Stage5TextInputRunner
    _startup_opportunity: ExecutionOpportunity | None = None

    def start(self) -> None:
        if self._startup_opportunity is not None:
            raise RuntimeError("stage-5 system already has a startup opportunity")
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

    def receive_text(self, raw: RawTextInput) -> Stage5InputOutcome:
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
            raise RuntimeError(f"cannot stop stage-5 system from {self.runtime.status}")
        self.state.stop()
        self.runtime.stop()
        self._startup_opportunity = None


def make_stage5_system(
    *,
    clock: Clock | None = None,
    runtime_id_factory: Callable[[], str] | None = None,
    time_correlation_id_factory: Callable[[], str] | None = None,
    input_correlation_id_factory: Callable[[], str] | None = None,
    reception_id_factory: Callable[[], str] | None = None,
    event_id_factory: Callable[[], str] | None = None,
    request_id_factory: Callable[[], str] | None = None,
    expression_request_id_factory: Callable[[], str] | None = None,
    expression_artifact_id_factory: Callable[[], str] | None = None,
    memory_available: bool = True,
    known_counterpart_id: str = "human-001",
    normal_dialogue_output_enabled: bool = True,
) -> Stage5System:
    runtime = Stage2Runtime(clock=clock, id_factory=runtime_id_factory)
    protection_boundary = Stage5ProtectionBoundary(
        normal_dialogue_output_enabled=normal_dialogue_output_enabled,
    )
    sensation = Stage3Sensation(
        reception_id_factory=reception_id_factory,
        event_id_factory=event_id_factory,
    )
    state = Stage4State()
    memory = Stage4Memory(available=memory_available)
    relationship = Stage4Relationship(
        known_counterpart_id=known_counterpart_id,
    )
    core = Stage5Core(
        state=state,
        memory=memory,
        relationship=relationship,
        request_id_factory=request_id_factory,
        expression_request_id_factory=expression_request_id_factory,
    )
    judgment = Stage4Judgment()
    language = Stage5Language(
        artifact_id_factory=expression_artifact_id_factory,
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
        correlation_id_factory=(
            time_correlation_id_factory or (lambda: str(uuid4()))
        ),
    )
    text_runner = Stage5TextInputRunner(
        protection_boundary=protection_boundary,
        sensation=sensation,
        core=core,
        judgment=judgment,
        language=language,
        correlation_id_factory=(
            input_correlation_id_factory or (lambda: str(uuid4()))
        ),
    )
    return Stage5System(
        composition=composition,
        runtime=runtime,
        state=state,
        sensation=sensation,
        judgment=judgment,
        language=language,
        core=core,
        protection_boundary=protection_boundary,
        time_cycle_runner=time_runner,
        text_input_runner=text_runner,
    )
