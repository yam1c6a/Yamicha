"""Judgment-owned comparison for a protection release proposal."""

from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from yamicha.contracts import (
    ExternalTime,
    ProtectionReleaseEvaluation,
    RecoveryEvidenceSource,
    RecoveryObservation,
)
from yamicha.life.stage6 import Stage6Judgment


class Stage8Judgment(Stage6Judgment):
    def __init__(
        self,
        *,
        release_evaluation_id_factory: Callable[[], str] | None = None,
        candidate_id_factory: Callable[[], str] | None = None,
    ) -> None:
        super().__init__(candidate_id_factory=candidate_id_factory)
        self._release_evaluation_id_factory = (
            release_evaluation_id_factory or (lambda: str(uuid4()))
        )
        self._release_evaluations: dict[str, ProtectionReleaseEvaluation] = {}

    def evaluate_protection_release(
        self,
        *,
        activation_id: str,
        definition_version: str,
        observations: tuple[RecoveryObservation, ...],
        evaluated_at: ExternalTime,
    ) -> ProtectionReleaseEvaluation:
        required = {
            RecoveryEvidenceSource.BODY,
            RecoveryEvidenceSource.STATE,
            RecoveryEvidenceSource.AFFECTED_ORGAN,
        }
        if (
            {observation.source for observation in observations} != required
            or len(observations) != len(required)
            or not all(observation.healthy for observation in observations)
            or any(observation.uncertainty is not None for observation in observations)
        ):
            raise ValueError("Judgment cannot approve incomplete recovery evidence")
        evaluation = ProtectionReleaseEvaluation(
            evaluation_id=self._release_evaluation_id_factory(),
            activation_id=activation_id,
            protection_definition_version=definition_version,
            observation_ids=tuple(
                observation.observation_id for observation in observations
            ),
            evaluated_at=evaluated_at,
        )
        self._release_evaluations[evaluation.evaluation_id] = evaluation
        return evaluation

    def issued_release_evaluation(
        self,
        evaluation_id: str,
    ) -> ProtectionReleaseEvaluation | None:
        return self._release_evaluations.get(evaluation_id)
