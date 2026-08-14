"""Compose stage 10's bounded local auxiliary intelligence."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, fields
from pathlib import Path
from typing import ClassVar
from uuid import uuid4

from yamicha.adapters.intelligence import OllamaChatAdapter
from yamicha.body.protection_boundary import Stage8ProtectionBoundary
from yamicha.body.persistence import SQLitePersistenceStore
from yamicha.body.runtime import Clock
from yamicha.contracts import (
    DecisionDirection,
    ExternalTime,
    FinalizationStatus,
    InputDisposition,
    InputQuality,
    InputRejection,
    IntelligenceAdoptionStatus,
    IntelligenceConstraints,
    IntelligenceTraceRecord,
    RawTextInput,
    Stage10InputOutcome,
)
from yamicha.life.stage10 import (
    Stage10AuxiliaryIntelligence,
    Stage10Core,
    Stage10Judgment,
    Stage10Language,
)
from yamicha.life.stage10.auxiliary_intelligence import IntelligenceTransport
from yamicha.life.stage9 import Stage9Sensation

from .composition import YamichaComposition
from .stage8 import Stage8SnapshotCoordinator, Stage8TextInputRunner
from .stage9 import Stage9System, make_stage9_system


CONFIGURATION_VERSION = "stage10-v1"
DEFAULT_OLLAMA_MODEL = "gemma4:e4b-it-qat"


class Stage10TextInputRunner(Stage8TextInputRunner):
    def __init__(
        self,
        *,
        protection_boundary: Stage8ProtectionBoundary,
        sensation: Stage9Sensation,
        core: Stage10Core,
        judgment: Stage10Judgment,
        language: Stage10Language,
        auxiliary_intelligence: Stage10AuxiliaryIntelligence,
        intelligence_model: str,
        intelligence_constraints: IntelligenceConstraints,
        correlation_id_factory: Callable[[], str],
        trace_id_factory: Callable[[], str] | None,
        persistence_store: SQLitePersistenceStore,
        snapshot_coordinator: Stage8SnapshotCoordinator,
        **kwargs: object,
    ) -> None:
        super().__init__(
            protection_boundary=protection_boundary,
            sensation=sensation,
            core=core,
            judgment=judgment,
            language=language,
            correlation_id_factory=correlation_id_factory,
            snapshot_coordinator=snapshot_coordinator,
            **kwargs,
        )
        self._stage10_boundary = protection_boundary
        self._stage10_sensation = sensation
        self._stage10_core = core
        self._stage10_judgment = judgment
        self._stage10_language = language
        self._stage10_auxiliary = auxiliary_intelligence
        self._intelligence_model = intelligence_model
        self._intelligence_constraints = intelligence_constraints
        self._stage10_correlation_id_factory = correlation_id_factory
        self._trace_id_factory = trace_id_factory or (lambda: str(uuid4()))
        self._persistence_store = persistence_store
        self._stage10_snapshot_coordinator = snapshot_coordinator
        self._used_stage10_correlation_ids: set[str] = set()

    def run(self, raw: RawTextInput) -> Stage10InputOutcome:
        if not self.persistence_healthy:
            raise RuntimeError("persistence is unavailable after a failed checkpoint")
        correlation_id = self._stage10_correlation_id_factory()
        if (
            not correlation_id.strip()
            or correlation_id in self._used_stage10_correlation_ids
        ):
            raise ValueError("input correlation ID must be non-empty and unique")
        self._used_stage10_correlation_ids.add(correlation_id)
        validation = self._stage10_boundary.validate(raw)
        if isinstance(validation, InputRejection):
            return Stage10InputOutcome(
                correlation_id=correlation_id,
                disposition=validation.disposition,
                rejection=validation,
            )
        event = self._stage10_sensation.receive_validated_text(
            validation,
            correlation_id,
        )
        cycle = self._stage10_core.accept_event(event)
        if event.quality is InputQuality.DUPLICATE:
            return Stage10InputOutcome(
                correlation_id=correlation_id,
                disposition=InputDisposition.DUPLICATE,
                cycle=cycle,
            )
        boundary = self._stage10_boundary.present_decision_material(event)
        context = self._stage10_core.build_judgment_context(cycle, boundary)
        judgment = self._stage10_judgment.evaluate(context)
        finalization = self._stage10_core.finalize_judgment(judgment, context)
        if finalization.status is not FinalizationStatus.FINALIZED:
            raise RuntimeError("stage-10 input requires a finalized judgment")
        expression_request = self._stage10_core.make_expression_request(
            finalization,
            judgment,
            context,
        )
        intelligence_request = None
        intelligence_result = None
        intelligence_review = None
        intelligence_adoption = None
        if (
            finalization.finalized_direction is DecisionDirection.RESPOND
            and len(event.meaning.normalized_text)
            <= self._intelligence_constraints.max_input_characters
        ):
            proposal = self._stage10_judgment.propose_dialogue_assistance(
                lifecycle_id=context.lifecycle_id,
                model=self._intelligence_model,
                input_text=event.meaning.normalized_text,
                input_source_reference=event.event_id,
                constraints=self._intelligence_constraints,
                proposed_at=raw.received_at,
            )
            intelligence_request = self._stage10_core.integrate_intelligence_request(
                proposal,
                finalization,
                raw.received_at,
            )
            intelligence_result = self._stage10_auxiliary.generate_candidate(
                intelligence_request,
                raw.received_at,
            )
            intelligence_review = self._stage10_judgment.review_intelligence_result(
                intelligence_result,
                raw.received_at,
            )
            intelligence_adoption = self._stage10_core.finalize_intelligence_adoption(
                request=intelligence_request,
                result=intelligence_result,
                review=intelligence_review,
                finalization=finalization,
                decided_at=raw.received_at,
            )
        if (
            intelligence_adoption is not None
            and intelligence_adoption.status is IntelligenceAdoptionStatus.ADOPTED
            and intelligence_result is not None
        ):
            expression = self._stage10_language.express_adopted_candidate(
                expression_request,
                intelligence_result,
                intelligence_adoption,
            )
            expression_review = self._stage10_core.review_intelligence_expression(
                expression_request,
                expression,
                intelligence_adoption,
                intelligence_result,
            )
        else:
            expression = self._stage10_language.express(expression_request)
            expression_review = self._stage10_core.review_expression(
                expression_request,
                expression,
            )
        dialogue_output = self._stage10_boundary.release_dialogue_output(
            expression,
            expression_review,
        )
        record = self._stage10_core.record_completed_lifecycle(
            context=context,
            judgment=judgment,
            finalization=finalization,
            expression=expression,
            expression_review=expression_review,
            dialogue_output=dialogue_output,
        )
        candidates = self._stage10_judgment.propose_retention(record)
        reviews = self._stage10_core.route_retention_candidates(candidates)
        outcome = Stage10InputOutcome(
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
            lifecycle_record=record,
            retention_candidates=candidates,
            candidate_reviews=reviews,
            intelligence_request=intelligence_request,
            intelligence_result=intelligence_result,
            intelligence_review=intelligence_review,
            intelligence_adoption=intelligence_adoption,
        )
        try:
            if (
                intelligence_request is not None
                and intelligence_result is not None
                and intelligence_adoption is not None
            ):
                self._store_trace(
                    intelligence_request,
                    intelligence_result,
                    intelligence_adoption,
                    raw.received_at,
                )
            self._stage10_snapshot_coordinator.commit(raw.received_at)
        except Exception:
            self.mark_persistence_unhealthy()
            raise
        return outcome

    def _store_trace(self, request, result, adoption, occurred_at: ExternalTime) -> None:
        proposal = request.proposal
        constraints_payload = json.dumps(
            {
                "max_input_characters": proposal.constraints.max_input_characters,
                "max_output_characters": proposal.constraints.max_output_characters,
                "timeout_seconds": proposal.constraints.timeout_seconds,
                "allowed_input_scope": proposal.constraints.allowed_input_scope,
                "output_format": proposal.constraints.output_format,
                "speaker_name": proposal.constraints.speaker_name,
                "forbidden_self_identification": (
                    proposal.constraints.forbidden_self_identification
                ),
                "external_effect_claims_allowed": (
                    proposal.constraints.external_effect_claims_allowed
                ),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        candidate = result.candidate
        trace_id = self._trace_id_factory()
        if not trace_id.strip():
            raise ValueError("intelligence trace ID must not be empty")
        trace = IntelligenceTraceRecord(
            trace_id=trace_id,
            request_id=request.request_id,
            lifecycle_id=proposal.lifecycle_id,
            purpose=proposal.purpose,
            model=proposal.model,
            input_scope=proposal.constraints.allowed_input_scope,
            input_digest=self._digest(proposal.input_text),
            constraints_digest=self._digest(constraints_payload),
            result_status=result.status,
            output_digest=(None if candidate is None else self._digest(candidate.text)),
            adoption_status=adoption.status,
            reason=f"{result.detail}; {adoption.reason}",
            occurred_at=occurred_at,
        )
        self._persistence_store.append_intelligence_trace(trace)

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(slots=True, kw_only=True)
class Stage10System(Stage9System):
    stage_label: ClassVar[int] = 10
    core: Stage10Core
    judgment: Stage10Judgment
    language: Stage10Language
    auxiliary_intelligence: Stage10AuxiliaryIntelligence
    text_input_runner: Stage10TextInputRunner
    intelligence_model: str
    intelligence_constraints: IntelligenceConstraints


def make_stage10_system(
    *,
    persistence_path: str | Path = Path(".yamicha/yamicha.sqlite3"),
    require_existing_persistence: bool = False,
    clock: Clock | None = None,
    persistence_time_factory: Callable[[], ExternalTime] | None = None,
    subject_id_factory: Callable[[], str] | None = None,
    session_id_factory: Callable[[], str] | None = None,
    input_correlation_id_factory: Callable[[], str] | None = None,
    intelligence_transport: IntelligenceTransport | None = None,
    intelligence_model: str = DEFAULT_OLLAMA_MODEL,
    ollama_endpoint: str = "http://127.0.0.1:11434/api/chat",
    intelligence_timeout_seconds: float = 60.0,
    intelligence_max_input_characters: int = 4096,
    intelligence_max_output_characters: int = 800,
    intelligence_result_id_factory: Callable[[], str] | None = None,
    intelligence_candidate_id_factory: Callable[[], str] | None = None,
    intelligence_proposal_id_factory: Callable[[], str] | None = None,
    intelligence_review_id_factory: Callable[[], str] | None = None,
    intelligence_request_id_factory: Callable[[], str] | None = None,
    intelligence_authorization_id_factory: Callable[[], str] | None = None,
    intelligence_adoption_id_factory: Callable[[], str] | None = None,
    intelligence_artifact_id_factory: Callable[[], str] | None = None,
    intelligence_trace_id_factory: Callable[[], str] | None = None,
    **stage9_options: object,
) -> Stage10System:
    constraints = IntelligenceConstraints(
        max_input_characters=intelligence_max_input_characters,
        max_output_characters=intelligence_max_output_characters,
        timeout_seconds=intelligence_timeout_seconds,
        allowed_input_scope=(
            "current_verified_text",
            "verified_speaker_and_model_identity",
        ),
        output_format='json:{"reply":"string"}',
        speaker_name="Yamicha",
        forbidden_self_identification=(
            "私は補助知能",
            "私はYamichaの補助知能",
            "私が補助知能",
            "Yamichaの補助知能です",
            "補助知能です",
            "私はAI",
            "AIです",
            "私はGemma",
            "Gemmaです",
            "私は言語モデル",
            "言語モデルです",
            "I am an AI",
            "I am Gemma",
            "I am a language model",
            "I am Yamicha's auxiliary intelligence",
        ),
        external_effect_claims_allowed=False,
    )
    base = make_stage9_system(
        persistence_path=persistence_path,
        require_existing_persistence=require_existing_persistence,
        clock=clock,
        persistence_time_factory=persistence_time_factory,
        subject_id_factory=subject_id_factory,
        session_id_factory=session_id_factory,
        input_correlation_id_factory=input_correlation_id_factory,
        _configuration_version=CONFIGURATION_VERSION,
        _upgrade_from_configuration_versions=("stage9-v1",),
        _core_factory=Stage10Core,
        _judgment_factory=Stage10Judgment,
        _language_factory=Stage10Language,
        _core_options={
            "intelligence_request_id_factory": intelligence_request_id_factory,
            "intelligence_authorization_id_factory": (
                intelligence_authorization_id_factory
            ),
            "intelligence_adoption_id_factory": intelligence_adoption_id_factory,
        },
        _judgment_options={
            "intelligence_proposal_id_factory": intelligence_proposal_id_factory,
            "intelligence_review_id_factory": intelligence_review_id_factory,
        },
        _language_options={
            "intelligence_artifact_id_factory": intelligence_artifact_id_factory,
        },
        **stage9_options,
    )
    try:
        base.persistence.initialize_intelligence_storage()
        base.persistence.intelligence_trace_records()
    except Exception:
        base.persistence.close()
        raise
    core = base.core
    judgment = base.judgment
    language = base.language
    if not isinstance(core, Stage10Core):
        raise TypeError("stage-10 Core was not composed")
    if not isinstance(judgment, Stage10Judgment):
        raise TypeError("stage-10 Judgment was not composed")
    if not isinstance(language, Stage10Language):
        raise TypeError("stage-10 Language was not composed")
    transport = intelligence_transport or OllamaChatAdapter(endpoint=ollama_endpoint)
    auxiliary = Stage10AuxiliaryIntelligence(
        transport,
        request_validator=core.issued_intelligence_request,
        result_id_factory=intelligence_result_id_factory,
        candidate_id_factory=intelligence_candidate_id_factory,
    )
    core.bind_intelligence_validators(
        proposal_validator=judgment.issued_intelligence_proposal,
        result_validator=auxiliary.issued,
        review_validator=judgment.issued_intelligence_review,
    )
    composition = YamichaComposition(
        core=core,
        memory=base.memory,
        state=base.state,
        sensation=base.sensation,
        judgment=judgment,
        relationship=base.relationship,
        capability=base.capability,
        language=language,
        auxiliary_intelligence=auxiliary,
        runtime=base.runtime,
        protection_boundary=base.protection_boundary,
        external_effect_gate=base.external_effect_gate,
    )
    text_runner = Stage10TextInputRunner(
        protection_boundary=base.protection_boundary,
        sensation=base.sensation,
        core=core,
        judgment=judgment,
        language=language,
        auxiliary_intelligence=auxiliary,
        intelligence_model=intelligence_model,
        intelligence_constraints=constraints,
        correlation_id_factory=input_correlation_id_factory or (lambda: str(uuid4())),
        trace_id_factory=intelligence_trace_id_factory,
        persistence_store=base.persistence,
        snapshot_coordinator=base.snapshot_coordinator,
    )
    base_values = {
        field.name: getattr(base, field.name)
        for field in fields(Stage9System)
    }
    base_values["composition"] = composition
    base_values["text_input_runner"] = text_runner
    return Stage10System(
        **base_values,
        auxiliary_intelligence=auxiliary,
        intelligence_model=intelligence_model,
        intelligence_constraints=constraints,
    )
