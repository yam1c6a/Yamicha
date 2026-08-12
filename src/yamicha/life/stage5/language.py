"""Deterministic stage-5 expression without external intelligence."""

from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from yamicha.contracts import (
    ExpressionArtifact,
    ExpressionMeaning,
    ExpressionMode,
    ExpressionRequest,
    MessageEnvelope,
    RenderedStatement,
    UnimplementedResponsibilityError,
)
from yamicha.life.ports import LANGUAGE_DEFINITION


class Stage5Language:
    definition = LANGUAGE_DEFINITION

    _TEMPLATES = {
        ExpressionMeaning.INPUT_RECEIVED: "入力を受け取りました。",
        ExpressionMeaning.EXTERNAL_EFFECT_POSSIBLE: (
            "外部への操作が必要な可能性があります。"
        ),
        ExpressionMeaning.EXTERNAL_EFFECT_NOT_EXECUTED: (
            "外部への操作はまだ実行していません。"
        ),
        ExpressionMeaning.TARGET_AUTHORITY_EFFECT_UNKNOWN: (
            "対象、権限、意図する効果が未確認です。"
        ),
        ExpressionMeaning.CONFIRM_TARGET_AUTHORITY_EFFECT: (
            "対象、権限、意図する効果を確認してください。"
        ),
        ExpressionMeaning.MATERIAL_INSUFFICIENT: (
            "判断に必要な情報が不足しています。"
        ),
        ExpressionMeaning.DECISION_HELD: "いまは保留します。",
        ExpressionMeaning.BOUNDARY_VIOLATION: (
            "関係上の境界に反する内容が含まれています。"
        ),
        ExpressionMeaning.REQUEST_REFUSED: (
            "境界を守るため、その要求には応じません。"
        ),
    }

    def __init__(
        self,
        *,
        artifact_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._artifact_id_factory = artifact_id_factory or (lambda: str(uuid4()))
        self._artifact_ids: set[str] = set()

    def express(self, request: ExpressionRequest) -> ExpressionArtifact:
        artifact_id = self._artifact_id_factory()
        if not artifact_id.strip() or artifact_id in self._artifact_ids:
            raise ValueError("expression artifact ID must be non-empty and unique")
        self._artifact_ids.add(artifact_id)

        if request.mode is ExpressionMode.SILENCE:
            return ExpressionArtifact(
                artifact_id=artifact_id,
                request_id=request.request_id,
                lifecycle_id=request.lifecycle_id,
                direction=request.direction,
                mode=request.mode,
                text=None,
                statements=(),
                claimed_completed_effects=(),
                external_intelligence_used=False,
            )

        statements = tuple(
            RenderedStatement(
                item=item,
                text=self._TEMPLATES[item.meaning],
            )
            for item in request.items
        )
        return ExpressionArtifact(
            artifact_id=artifact_id,
            request_id=request.request_id,
            lifecycle_id=request.lifecycle_id,
            direction=request.direction,
            mode=request.mode,
            text=" ".join(statement.text for statement in statements),
            statements=statements,
            claimed_completed_effects=(),
            external_intelligence_used=False,
        )

    def handle(self, message: MessageEnvelope) -> MessageEnvelope:
        raise UnimplementedResponsibilityError(
            "generic language message handling starts after stage 5"
        )
