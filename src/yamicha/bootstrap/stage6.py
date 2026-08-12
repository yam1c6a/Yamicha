"""Compose stage-6 records, retention candidates, and Memory review."""

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
    InputDisposition,
    RawTextInput,
    Stage6InputOutcome,
)
from yamicha.life.stage3 import Stage3Sensation
from yamicha.life.stage4 import Stage4Relationship, Stage4State
from yamicha.life.stage5 import Stage5Language
from yamicha.life.stage6 import Stage6Core, Stage6Judgment, Stage6Memory
from yamicha.life.stubs import AuxiliaryIntelligenceStub, CapabilityStub

from .composition import YamichaComposition
from .stage2 import InputFreeCycleRunner
from .stage5 import Stage5TextInputRunner


class Stage6TextInputRunner(Stage5TextInputRunner):
    def __init__(
        self,
        *,
        protection_boundary: Stage5ProtectionBoundary,
        sensation: Stage3Sensation,
        core: Stage6Core,
        judgment: Stage6Judgment,
        language: Stage5Language,
        correlation_id_factory: Callable[[], str],
    ) -> None:
        super().__init__(
            protection_boundary=protection_boundary,
            sensation=sensation,
            core=core,
            judgment=judgment,
            language=language,
            correlation_id_factory=correlation_id_factory,
        )
        self._stage6_core = core
        self._stage6_judgment = judgment

    def run(self, raw: RawTextInput) -> Stage6InputOutcome:
        base = super().run(raw)
        if base.disposition is not InputDisposition.ACCEPTED:
            return Stage6InputOutcome(
                correlation_id=base.correlation_id,
                disposition=base.disposition,
                rejection=base.rejection,
                cycle=base.cycle,
                context=base.context,
                judgment=base.judgment,
                finalization=base.finalization,
                expression_request=base.expression_request,
                expression=base.expression,
                expression_review=base.expression_review,
                dialogue_output=base.dialogue_output,
            )
        if any(
            value is None
            for value in (
                base.context,
                base.judgment,
                base.finalization,
                base.expression,
                base.expression_review,
                base.dialogue_output,
            )
        ):
            raise RuntimeError("stage-6 retention requires a completed expression path")
        assert base.context is not None
        assert base.judgment is not None
        assert base.finalization is not None
        assert base.expression is not None
        assert base.expression_review is not None
        assert base.dialogue_output is not None
        record = self._stage6_core.record_completed_lifecycle(
            context=base.context,
            judgment=base.judgment,
            finalization=base.finalization,
            expression=base.expression,
            expression_review=base.expression_review,
            dialogue_output=base.dialogue_output,
        )
        candidates = self._stage6_judgment.propose_retention(record)
        reviews = self._stage6_core.route_retention_candidates(candidates)
        return Stage6InputOutcome(
            correlation_id=base.correlation_id,
            disposition=base.disposition,
            cycle=base.cycle,
            context=base.context,
            judgment=base.judgment,
            finalization=base.finalization,
            expression_request=base.expression_request,
            expression=base.expression,
            expression_review=base.expression_review,
            dialogue_output=base.dialogue_output,
            lifecycle_record=record,
            retention_candidates=candidates,
            candidate_reviews=reviews,
        )


@dataclass(slots=True)
class Stage6System:
    composition: YamichaComposition
    runtime: Stage2Runtime
    state: Stage4State
    memory: Stage6Memory
    sensation: Stage3Sensation
    judgment: Stage6Judgment
    language: Stage5Language
    core: Stage6Core
    protection_boundary: Stage5ProtectionBoundary
    time_cycle_runner: InputFreeCycleRunner
    text_input_runner: Stage6TextInputRunner
    _startup_opportunity: ExecutionOpportunity | None = None

    def start(self) -> None:
        if self._startup_opportunity is not None:
            raise RuntimeError("stage-6 system already has a startup opportunity")
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

    def receive_text(self, raw: RawTextInput) -> Stage6InputOutcome:
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
            raise RuntimeError(f"cannot stop stage-6 system from {self.runtime.status}")
        self.state.stop()
        self.runtime.stop()
        self._startup_opportunity = None


def make_stage6_system(
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
    lifecycle_record_id_factory: Callable[[], str] | None = None,
    record_entry_id_factory: Callable[[], str] | None = None,
    retention_candidate_id_factory: Callable[[], str] | None = None,
    candidate_review_id_factory: Callable[[], str] | None = None,
    memory_item_id_factory: Callable[[], str] | None = None,
    memory_available: bool = True,
    known_counterpart_id: str = "human-001",
    normal_dialogue_output_enabled: bool = True,
) -> Stage6System:
    runtime = Stage2Runtime(clock=clock, id_factory=runtime_id_factory)
    protection_boundary = Stage5ProtectionBoundary(
        normal_dialogue_output_enabled=normal_dialogue_output_enabled,
    )
    sensation = Stage3Sensation(
        reception_id_factory=reception_id_factory,
        event_id_factory=event_id_factory,
    )
    state = Stage4State()
    memory = Stage6Memory(
        available=memory_available,
        review_id_factory=candidate_review_id_factory,
        memory_item_id_factory=memory_item_id_factory,
    )
    relationship = Stage4Relationship(
        known_counterpart_id=known_counterpart_id,
    )
    core = Stage6Core(
        state=state,
        memory=memory,
        relationship=relationship,
        request_id_factory=request_id_factory,
        expression_request_id_factory=expression_request_id_factory,
        lifecycle_record_id_factory=lifecycle_record_id_factory,
        record_entry_id_factory=record_entry_id_factory,
    )
    judgment = Stage6Judgment(
        candidate_id_factory=retention_candidate_id_factory,
    )
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
    text_runner = Stage6TextInputRunner(
        protection_boundary=protection_boundary,
        sensation=sensation,
        core=core,
        judgment=judgment,
        language=language,
        correlation_id_factory=(
            input_correlation_id_factory or (lambda: str(uuid4()))
        ),
    )
    return Stage6System(
        composition=composition,
        runtime=runtime,
        state=state,
        memory=memory,
        sensation=sensation,
        judgment=judgment,
        language=language,
        core=core,
        protection_boundary=protection_boundary,
        time_cycle_runner=time_runner,
        text_input_runner=text_runner,
    )
