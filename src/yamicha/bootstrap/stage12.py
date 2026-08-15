"""Compose stage 12's bounded, persistent dialogue context."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, fields
from typing import ClassVar
from uuid import uuid4

from yamicha.body.runtime import RuntimeStatus
from yamicha.contracts import (
    AuxiliaryIntelligenceProposal,
    DialogueContext,
    DialogueContextWindow,
    DialogueOutput,
    ExternalTime,
    JudgmentContext,
    ProtectionMode,
    RawTextInput,
    SensoryEvent,
    Stage10InputOutcome,
)
from yamicha.life.stage12 import Stage12Relationship

from .composition import YamichaComposition
from .stage10 import (
    Stage10System,
    Stage10TextInputRunner,
    make_stage10_system,
)


CONFIGURATION_VERSION = "stage12-v1"
DEFAULT_DIALOGUE_MAX_EXCHANGES = 6


class Stage12TextInputRunner(Stage10TextInputRunner):
    def __init__(
        self,
        *,
        relationship: Stage12Relationship,
        dialogue_max_exchanges: int,
        **kwargs: object,
    ) -> None:
        if dialogue_max_exchanges <= 0:
            raise ValueError("dialogue exchange limit must be positive")
        super().__init__(**kwargs)
        self._stage12_relationship = relationship
        self._dialogue_max_exchanges = dialogue_max_exchanges
        self._last_dialogue_window: DialogueContextWindow | None = None

    @property
    def last_dialogue_window(self) -> DialogueContextWindow | None:
        return self._last_dialogue_window

    def run(self, raw: RawTextInput) -> Stage10InputOutcome:
        self._last_dialogue_window = None
        return super().run(raw)

    def _prepare_dialogue_context(self, event: SensoryEvent) -> None:
        self._stage12_relationship.ensure_active_context(event)

    def _propose_dialogue_assistance(
        self,
        context: JudgmentContext,
        event: SensoryEvent,
        proposed_at: ExternalTime,
    ) -> AuxiliaryIntelligenceProposal:
        window = self._stage12_relationship.select_dialogue_window(
            event,
            max_exchanges=self._dialogue_max_exchanges,
            max_characters=self._intelligence_constraints.max_input_characters,
        )
        self._last_dialogue_window = window
        return self._stage10_judgment.propose_dialogue_assistance(
            lifecycle_id=context.lifecycle_id,
            model=self._intelligence_model,
            input_text=event.meaning.normalized_text,
            input_source_reference=event.event_id,
            constraints=self._intelligence_constraints,
            proposed_at=proposed_at,
            dialogue_context=window,
        )

    def _record_dialogue_context(
        self,
        event: SensoryEvent,
        dialogue_output: DialogueOutput,
    ) -> None:
        self._stage12_relationship.record_completed_exchange(
            event,
            dialogue_output,
        )


@dataclass(slots=True, kw_only=True)
class Stage12System(Stage10System):
    stage_label: ClassVar[int] = 12
    relationship: Stage12Relationship
    text_input_runner: Stage12TextInputRunner
    dialogue_max_exchanges: int

    def start_new_dialogue_context(self, started_at: ExternalTime) -> DialogueContext:
        if self._closed:
            raise RuntimeError("stage-12 system is closed")
        if not self.text_input_runner.persistence_healthy:
            raise RuntimeError("persistence is unavailable after a failed checkpoint")
        if self.protection_boundary.mode is ProtectionMode.PROTECTED:
            raise RuntimeError("new dialogue context is blocked while protection is active")
        if self.runtime.status is RuntimeStatus.NOT_STARTED or (
            self._startup_opportunity is not None
        ):
            self.run_time_cycle()
        if self.runtime.status is not RuntimeStatus.WAITING:
            raise RuntimeError("new dialogue context requires waiting runtime")
        context = self.relationship.start_new_context(started_at)
        try:
            self.snapshot_coordinator.commit(started_at)
        except Exception:
            self.text_input_runner.mark_persistence_unhealthy()
            raise
        return context


def make_stage12_system(
    *,
    dialogue_max_exchanges: int = DEFAULT_DIALOGUE_MAX_EXCHANGES,
    dialogue_context_id_factory: Callable[[], str] | None = None,
    dialogue_turn_id_factory: Callable[[], str] | None = None,
    **stage10_options: object,
) -> Stage12System:
    if dialogue_max_exchanges <= 0:
        raise ValueError("dialogue exchange limit must be positive")
    base = make_stage10_system(
        _configuration_version=CONFIGURATION_VERSION,
        _upgrade_from_configuration_versions=("stage10-v1",),
        _intelligence_allowed_input_scope=(
            "current_verified_text",
            "verified_recent_dialogue_turns",
            "verified_speaker_and_model_identity",
        ),
        _relationship_factory=Stage12Relationship,
        _relationship_options={
            "context_id_factory": dialogue_context_id_factory,
            "turn_id_factory": dialogue_turn_id_factory,
        },
        **stage10_options,
    )
    relationship = base.relationship
    if not isinstance(relationship, Stage12Relationship):
        raise TypeError("stage-12 Relationship was not composed")
    runner = Stage12TextInputRunner(
        protection_boundary=base.protection_boundary,
        sensation=base.sensation,
        core=base.core,
        judgment=base.judgment,
        language=base.language,
        auxiliary_intelligence=base.auxiliary_intelligence,
        intelligence_model=base.intelligence_model,
        intelligence_constraints=base.intelligence_constraints,
        correlation_id_factory=stage10_options.get("input_correlation_id_factory")
        or (lambda: str(uuid4())),
        trace_id_factory=stage10_options.get("intelligence_trace_id_factory"),
        persistence_store=base.persistence,
        snapshot_coordinator=base.snapshot_coordinator,
        relationship=relationship,
        dialogue_max_exchanges=dialogue_max_exchanges,
    )
    composition = YamichaComposition(
        core=base.core,
        memory=base.memory,
        state=base.state,
        sensation=base.sensation,
        judgment=base.judgment,
        relationship=relationship,
        capability=base.capability,
        language=base.language,
        auxiliary_intelligence=base.auxiliary_intelligence,
        runtime=base.runtime,
        protection_boundary=base.protection_boundary,
        external_effect_gate=base.external_effect_gate,
    )
    values = {field.name: getattr(base, field.name) for field in fields(Stage10System)}
    values["composition"] = composition
    values["text_input_runner"] = runner
    return Stage12System(
        **values,
        dialogue_max_exchanges=dialogue_max_exchanges,
    )
