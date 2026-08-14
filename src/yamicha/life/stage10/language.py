"""Language rendering of a Core-adopted intelligence candidate."""

from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from yamicha.contracts import (
    AuxiliaryIntelligenceResult,
    ExpressionArtifact,
    ExpressionMode,
    ExpressionRequest,
    IntelligenceAdoption,
    IntelligenceAdoptionStatus,
    RenderedStatement,
)
from yamicha.life.stage5 import Stage5Language


class Stage10Language(Stage5Language):
    def __init__(
        self,
        *,
        intelligence_artifact_id_factory: Callable[[], str] | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self._intelligence_artifact_id_factory = (
            intelligence_artifact_id_factory or (lambda: str(uuid4()))
        )
        self._intelligence_artifact_ids: set[str] = set()

    def express_adopted_candidate(
        self,
        request: ExpressionRequest,
        result: AuxiliaryIntelligenceResult,
        adoption: IntelligenceAdoption,
    ) -> ExpressionArtifact:
        candidate = result.candidate
        if (
            adoption.status is not IntelligenceAdoptionStatus.ADOPTED
            or candidate is None
            or adoption.candidate_id != candidate.candidate_id
            or request.mode is not ExpressionMode.RESPONSE
            or len(request.items) != 1
        ):
            raise ValueError("Language requires one adopted response candidate")
        artifact_id = self._intelligence_artifact_id_factory()
        if (
            not artifact_id.strip()
            or artifact_id in self._intelligence_artifact_ids
        ):
            raise ValueError("intelligence expression ID must be non-empty and unique")
        self._intelligence_artifact_ids.add(artifact_id)
        return ExpressionArtifact(
            artifact_id=artifact_id,
            request_id=request.request_id,
            lifecycle_id=request.lifecycle_id,
            direction=request.direction,
            mode=request.mode,
            text=candidate.text,
            statements=(
                RenderedStatement(
                    item=request.items[0],
                    text=candidate.text,
                ),
            ),
            claimed_completed_effects=(),
            external_intelligence_used=True,
        )
