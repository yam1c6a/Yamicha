"""Compose stage 9's first integrated read-only capability."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, fields
from pathlib import Path
from typing import ClassVar

from yamicha.adapters.resources import BoundedTextFileReader
from yamicha.body.external_effect_gate import (
    RegisteredCapabilityPermissionObserver,
    Stage9ExternalEffectGate,
)
from yamicha.body.runtime import Clock
from yamicha.contracts import (
    CapabilityDispatchStatus,
    ExternalTime,
    ProtectionMode,
    READ_ONLY_EXPECTED_EFFECT,
    Stage9CapabilityOutcome,
)
from yamicha.life.stage9 import (
    ReadOnlyCapability,
    Stage9Core,
    Stage9Judgment,
    Stage9Sensation,
)
from yamicha.life.stage9.capability import ReadOnlyResourceReader
from yamicha.life.stage5 import Stage5Language
from yamicha.life.stubs import AuxiliaryIntelligenceStub

from .composition import YamichaComposition
from .stage8 import Stage8System, make_stage8_system


CONFIGURATION_VERSION = "stage9-v1"


@dataclass(slots=True, kw_only=True)
class Stage9System(Stage8System):
    stage_label: ClassVar[int] = 9
    core: Stage9Core
    judgment: Stage9Judgment
    sensation: Stage9Sensation
    capability: ReadOnlyCapability
    external_effect_gate: Stage9ExternalEffectGate
    capability_permission_observer: RegisteredCapabilityPermissionObserver

    def use_read_capability(
        self,
        *,
        target: str,
        authority_id: str,
        idempotency_key: str,
        requested_at: ExternalTime,
        reason: str = "an external text resource is required as judgment material",
    ) -> Stage9CapabilityOutcome:
        proposal = self.judgment.propose_read_capability(
            target=target,
            authority_id=authority_id,
            expected_effect=READ_ONLY_EXPECTED_EFFECT,
            idempotency_key=idempotency_key,
            reason=reason,
            proposed_at=requested_at,
        )
        request = self.core.integrate_capability_request(proposal, requested_at)
        permission = self.capability_permission_observer.observe(
            request,
            requested_at,
        )
        gate_outcome = self.external_effect_gate.authorize(
            request,
            permission,
            requested_at,
        )
        if gate_outcome.permit is None:
            return Stage9CapabilityOutcome(
                dispatch_status=(
                    CapabilityDispatchStatus.DUPLICATE
                    if gate_outcome.duplicate
                    else CapabilityDispatchStatus.REJECTED
                ),
                request=request,
                reason=gate_outcome.reason,
            )
        result = self.capability.execute(gate_outcome.permit, requested_at)
        self.external_effect_gate.record_result(result)
        event = self.sensation.receive_capability_result(result, requested_at)
        self.core.receive_capability_result(event)
        return Stage9CapabilityOutcome(
            dispatch_status=CapabilityDispatchStatus.EXECUTED,
            request=request,
            result=result,
            event=event,
        )


def make_stage9_system(
    *,
    persistence_path: str | Path = Path(".yamicha/yamicha.sqlite3"),
    require_existing_persistence: bool = False,
    clock: Clock | None = None,
    persistence_time_factory: Callable[[], ExternalTime] | None = None,
    subject_id_factory: Callable[[], str] | None = None,
    session_id_factory: Callable[[], str] | None = None,
    capability_reader: ReadOnlyResourceReader | None = None,
    capability_root: str | Path = Path("."),
    capability_max_characters: int = 16_384,
    capability_authority_id: str = "local-operator",
    authorized_read_targets: tuple[str, ...] = ("README.md",),
    capability_permission_observation_id_factory: Callable[[], str] | None = None,
    capability_permit_id_factory: Callable[[], str] | None = None,
    capability_result_id_factory: Callable[[], str] | None = None,
    capability_event_id_factory: Callable[[], str] | None = None,
    capability_proposal_id_factory: Callable[[], str] | None = None,
    capability_request_id_factory: Callable[[], str] | None = None,
    capability_finalization_id_factory: Callable[[], str] | None = None,
    _configuration_version: str = CONFIGURATION_VERSION,
    _upgrade_from_configuration_versions: tuple[str, ...] = ("stage8-v1",),
    _core_factory: Callable[..., Stage9Core] = Stage9Core,
    _judgment_factory: Callable[..., Stage9Judgment] = Stage9Judgment,
    _language_factory: Callable[..., Stage5Language] = Stage5Language,
    _core_options: dict[str, object] | None = None,
    _judgment_options: dict[str, object] | None = None,
    _language_options: dict[str, object] | None = None,
    **stage8_options: object,
) -> Stage9System:
    core_options = {
        "capability_request_id_factory": capability_request_id_factory,
        "capability_finalization_id_factory": capability_finalization_id_factory,
    }
    core_options.update(_core_options or {})
    judgment_options = {
        "capability_proposal_id_factory": capability_proposal_id_factory,
    }
    judgment_options.update(_judgment_options or {})
    base = make_stage8_system(
        persistence_path=persistence_path,
        require_existing_persistence=require_existing_persistence,
        clock=clock,
        persistence_time_factory=persistence_time_factory,
        subject_id_factory=subject_id_factory,
        session_id_factory=session_id_factory,
        _configuration_version=_configuration_version,
        _upgrade_from_configuration_versions=(
            _upgrade_from_configuration_versions
        ),
        _sensation_factory=Stage9Sensation,
        _core_factory=_core_factory,
        _judgment_factory=_judgment_factory,
        _language_factory=_language_factory,
        _sensation_options={
            "capability_event_id_factory": capability_event_id_factory,
        },
        _core_options=core_options,
        _judgment_options=judgment_options,
        _language_options=_language_options,
        **stage8_options,
    )
    try:
        base.persistence.initialize_capability_storage()
        base.persistence.capability_execution_records()
    except Exception:
        base.persistence.close()
        raise
    core = base.core
    judgment = base.judgment
    sensation = base.sensation
    if not isinstance(core, Stage9Core):
        raise TypeError("stage-9 Core was not composed")
    if not isinstance(judgment, Stage9Judgment):
        raise TypeError("stage-9 Judgment was not composed")
    if not isinstance(sensation, Stage9Sensation):
        raise TypeError("stage-9 Sensation was not composed")
    permission_observer = RegisteredCapabilityPermissionObserver(
        {capability_authority_id: authorized_read_targets},
        observation_id_factory=(
            capability_permission_observation_id_factory
        ),
    )
    gate = Stage9ExternalEffectGate(
        store=base.persistence,
        core_request_validator=(
            lambda request: core.issued_capability_request(request.request_id)
            == request
        ),
        judgment_proposal_validator=(
            lambda proposal: judgment.issued_capability_proposal(
                proposal.proposal_id
            )
            == proposal
        ),
        permission_validator=permission_observer.issued,
        normal_operation_allowed=(
            lambda: base.protection_boundary.mode is ProtectionMode.NORMAL
        ),
        permit_id_factory=capability_permit_id_factory,
    )
    reader = capability_reader or BoundedTextFileReader(
        capability_root,
        max_characters=capability_max_characters,
    )
    capability = ReadOnlyCapability(
        reader,
        permit_validator=gate.issued,
        result_id_factory=capability_result_id_factory,
    )
    composition = YamichaComposition(
        core=core,
        memory=base.memory,
        state=base.state,
        sensation=sensation,
        judgment=judgment,
        relationship=base.relationship,
        capability=capability,
        language=base.language,
        auxiliary_intelligence=AuxiliaryIntelligenceStub(),
        runtime=base.runtime,
        protection_boundary=base.protection_boundary,
        external_effect_gate=gate,
    )
    base_values = {
        field.name: getattr(base, field.name)
        for field in fields(Stage8System)
    }
    base_values["composition"] = composition
    return Stage9System(
        **base_values,
        capability=capability,
        external_effect_gate=gate,
        capability_permission_observer=permission_observer,
    )
