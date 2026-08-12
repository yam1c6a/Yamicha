"""Core integration and consistency review for stage-5 expression."""

from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from yamicha.contracts import (
    DecisionDirection,
    ExpressionArtifact,
    ExpressionItem,
    ExpressionMeaning,
    ExpressionMode,
    ExpressionRequest,
    ExpressionReview,
    ExpressionReviewStatus,
    FinalizationStatus,
    JudgmentContext,
    JudgmentFinalization,
    JudgmentResult,
    StatementKind,
    expression_mode_for,
)
from yamicha.life.ports import CORE_DEFINITION
from yamicha.life.stage4 import (
    Stage4Core,
    Stage4Memory,
    Stage4Relationship,
    Stage4State,
)


class Stage5Core(Stage4Core):
    definition = CORE_DEFINITION

    def __init__(
        self,
        *,
        state: Stage4State,
        memory: Stage4Memory,
        relationship: Stage4Relationship,
        request_id_factory: Callable[[], str] | None = None,
        expression_request_id_factory: Callable[[], str] | None = None,
    ) -> None:
        super().__init__(
            state=state,
            memory=memory,
            relationship=relationship,
            request_id_factory=request_id_factory,
        )
        self._expression_request_id_factory = (
            expression_request_id_factory or (lambda: str(uuid4()))
        )
        self._expression_request_ids: set[str] = set()

    def make_expression_request(
        self,
        finalization: JudgmentFinalization,
        judgment: JudgmentResult,
        context: JudgmentContext,
    ) -> ExpressionRequest:
        if finalization.status is not FinalizationStatus.FINALIZED:
            raise ValueError("only finalized judgment may reach Language")
        direction = finalization.finalized_direction
        if direction is None:
            raise ValueError("finalized judgment has no direction")
        if (
            finalization.lifecycle_id != judgment.lifecycle_id
            or judgment.lifecycle_id != context.lifecycle_id
            or direction is not judgment.selected_direction
        ):
            raise ValueError("expression inputs do not identify one finalized decision")

        request_id = self._expression_request_id_factory()
        if not request_id.strip() or request_id in self._expression_request_ids:
            raise ValueError("expression request ID must be non-empty and unique")
        self._expression_request_ids.add(request_id)
        mode = expression_mode_for(direction)
        return ExpressionRequest(
            request_id=request_id,
            lifecycle_id=context.lifecycle_id,
            decision_reference=(
                f"judgment:{judgment.lifecycle_id}:{direction.value}"
            ),
            direction=direction,
            mode=mode,
            items=self._expression_items(direction, judgment, context),
            silence_required=mode is ExpressionMode.SILENCE,
            confirmed_effects=(),
        )

    def review_expression(
        self,
        request: ExpressionRequest,
        artifact: ExpressionArtifact,
    ) -> ExpressionReview:
        if (
            artifact.lifecycle_id != request.lifecycle_id
            or artifact.request_id != request.request_id
            or artifact.direction is not request.direction
            or artifact.mode is not request.mode
        ):
            return self._reexpression(
                request,
                artifact,
                "expression does not preserve the integrated decision",
            )
        rendered_items = tuple(statement.item for statement in artifact.statements)
        if rendered_items != request.items:
            return self._reexpression(
                request,
                artifact,
                "expression changed or omitted integrated meaning items",
            )
        if any(
            effect not in request.confirmed_effects
            for effect in artifact.claimed_completed_effects
        ):
            return self._reexpression(
                request,
                artifact,
                "expression claims an effect that is not confirmed",
            )
        if artifact.external_intelligence_used:
            return self._reexpression(
                request,
                artifact,
                "stage-5 expression must not use external intelligence",
            )
        return ExpressionReview(
            lifecycle_id=request.lifecycle_id,
            request_id=request.request_id,
            artifact_id=artifact.artifact_id,
            direction=request.direction,
            status=ExpressionReviewStatus.ACCEPTED,
            reason="expression preserves direction, meaning items, and effect state",
        )

    @staticmethod
    def _expression_items(
        direction: DecisionDirection,
        judgment: JudgmentResult,
        context: JudgmentContext,
    ) -> tuple[ExpressionItem, ...]:
        decision_reference = f"judgment:{judgment.lifecycle_id}:{direction.value}"
        if direction is DecisionDirection.RESPOND:
            return (
                ExpressionItem(
                    kind=StatementKind.FACT,
                    meaning=ExpressionMeaning.INPUT_RECEIVED,
                    source_reference=context.event.event_id,
                ),
            )
        if direction is DecisionDirection.CONFIRM:
            return (
                ExpressionItem(
                    kind=StatementKind.INFERENCE,
                    meaning=ExpressionMeaning.EXTERNAL_EFFECT_POSSIBLE,
                    source_reference=decision_reference,
                ),
                ExpressionItem(
                    kind=StatementKind.FACT,
                    meaning=ExpressionMeaning.EXTERNAL_EFFECT_NOT_EXECUTED,
                    source_reference=context.boundary.version,
                ),
                ExpressionItem(
                    kind=StatementKind.UNKNOWN,
                    meaning=ExpressionMeaning.TARGET_AUTHORITY_EFFECT_UNKNOWN,
                    source_reference=decision_reference,
                ),
                ExpressionItem(
                    kind=StatementKind.CONFIRMATION_REQUEST,
                    meaning=ExpressionMeaning.CONFIRM_TARGET_AUTHORITY_EFFECT,
                    source_reference=decision_reference,
                ),
            )
        if direction is DecisionDirection.HOLD:
            return (
                ExpressionItem(
                    kind=StatementKind.UNKNOWN,
                    meaning=ExpressionMeaning.MATERIAL_INSUFFICIENT,
                    source_reference=decision_reference,
                ),
                ExpressionItem(
                    kind=StatementKind.HOLD,
                    meaning=ExpressionMeaning.DECISION_HELD,
                    source_reference=decision_reference,
                ),
            )
        if direction is DecisionDirection.REFUSE:
            return (
                ExpressionItem(
                    kind=StatementKind.FACT,
                    meaning=ExpressionMeaning.BOUNDARY_VIOLATION,
                    source_reference=context.relationship.version,
                ),
                ExpressionItem(
                    kind=StatementKind.REFUSAL,
                    meaning=ExpressionMeaning.REQUEST_REFUSED,
                    source_reference=decision_reference,
                ),
            )
        return ()

    @staticmethod
    def _reexpression(
        request: ExpressionRequest,
        artifact: ExpressionArtifact,
        reason: str,
    ) -> ExpressionReview:
        return ExpressionReview(
            lifecycle_id=request.lifecycle_id,
            request_id=request.request_id,
            artifact_id=artifact.artifact_id,
            direction=request.direction,
            status=ExpressionReviewStatus.REEXPRESSION_REQUIRED,
            reason=reason,
        )
