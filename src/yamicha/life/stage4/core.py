"""Core consistency checks for stage-4 judgment results."""

from __future__ import annotations

from collections.abc import Callable

from yamicha.contracts import (
    BoundaryDecisionMaterial,
    ContentTrust,
    DecisionDirection,
    FinalizationStatus,
    JudgmentContext,
    JudgmentFinalization,
    JudgmentResult,
    OperatingState,
    RequestStatus,
    RoutedInputCycle,
)
from yamicha.life.ports import CORE_DEFINITION
from yamicha.life.stage3 import Stage3Core

from .materials import Stage4Memory, Stage4Relationship, Stage4State


class Stage4Core(Stage3Core):
    definition = CORE_DEFINITION

    def __init__(
        self,
        *,
        state: Stage4State,
        memory: Stage4Memory,
        relationship: Stage4Relationship,
        request_id_factory: Callable[[], str] | None = None,
    ) -> None:
        super().__init__(
            {
                state.definition.identifier: state,
                memory.definition.identifier: memory,
                relationship.definition.identifier: relationship,
            },
            request_id_factory=request_id_factory,
        )
        self._state_material_provider = state
        self._memory_material_provider = memory
        self._relationship_material_provider = relationship

    def build_judgment_context(
        self,
        cycle: RoutedInputCycle,
        boundary: BoundaryDecisionMaterial,
    ) -> JudgmentContext:
        if cycle.lifecycle_id != boundary.lifecycle_id:
            raise ValueError("boundary material belongs to another lifecycle")
        if not cycle.responses or any(
            response.status is not RequestStatus.SUCCEEDED
            for response in cycle.responses
        ):
            raise ValueError("judgment requires successful reference responses")
        event = cycle.event
        return JudgmentContext(
            lifecycle_id=cycle.lifecycle_id,
            event=event,
            state=self._state_material_provider.present_decision_material(event),
            memory=self._memory_material_provider.present_decision_material(event),
            relationship=(
                self._relationship_material_provider.present_decision_material(event)
            ),
            boundary=boundary,
            auxiliary_intelligence_available=False,
        )

    def finalize_judgment(
        self,
        result: JudgmentResult,
        context: JudgmentContext,
    ) -> JudgmentFinalization:
        if result.lifecycle_id != context.lifecycle_id:
            return self._remand(result, "judgment belongs to another lifecycle")
        if result.material_versions != context.material_versions:
            return self._remand(result, "judgment materials are stale or inconsistent")
        if (
            not context.boundary.input_validated
            or context.boundary.content_trust is not ContentTrust.UNTRUSTED
            or context.boundary.external_effects_permitted
        ):
            return self._remand(result, "protection-boundary conditions are inconsistent")
        if context.state.operating_state is not OperatingState.WAITING:
            return self._remand(result, "current state is not ready for input judgment")

        materials_missing = (
            not context.state.available
            or not context.memory.available
            or not context.relationship.available
            or not context.relationship.counterpart_known
        )
        if materials_missing and result.selected_direction is not DecisionDirection.HOLD:
            return self._remand(
                result,
                "insufficient material may only be returned as hold",
            )
        if (
            not materials_missing
            and context.relationship.boundary_violation
            and result.selected_direction is not DecisionDirection.REFUSE
        ):
            return self._remand(
                result,
                "a relationship boundary violation may only be returned as refusal",
            )
        return JudgmentFinalization(
            lifecycle_id=result.lifecycle_id,
            status=FinalizationStatus.FINALIZED,
            proposed_direction=result.selected_direction,
            finalized_direction=result.selected_direction,
            reason="proposal is consistent with the supplied materials and boundary",
        )

    @staticmethod
    def _remand(result: JudgmentResult, reason: str) -> JudgmentFinalization:
        return JudgmentFinalization(
            lifecycle_id=result.lifecycle_id,
            status=FinalizationStatus.REMANDED,
            proposed_direction=result.selected_direction,
            finalized_direction=None,
            reason=reason,
        )
