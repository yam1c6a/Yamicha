"""Fail-closed output release for stage-5 direct dialogue."""

from __future__ import annotations

from yamicha.contracts import (
    DialogueOutput,
    ExpressionArtifact,
    ExpressionMode,
    ExpressionReview,
    ExpressionReviewStatus,
    OutputReleaseStatus,
)

from .stage4 import Stage4ProtectionBoundary


class Stage5ProtectionBoundary(Stage4ProtectionBoundary):
    def __init__(
        self,
        *,
        max_text_length: int = 4096,
        normal_dialogue_output_enabled: bool = True,
    ) -> None:
        super().__init__(max_text_length=max_text_length)
        self._normal_dialogue_output_enabled = normal_dialogue_output_enabled

    def release_dialogue_output(
        self,
        artifact: ExpressionArtifact,
        review: ExpressionReview,
    ) -> DialogueOutput:
        matching_review = (
            review.lifecycle_id == artifact.lifecycle_id
            and review.request_id == artifact.request_id
            and review.artifact_id == artifact.artifact_id
            and review.direction is artifact.direction
        )
        if (
            not matching_review
            or review.status is not ExpressionReviewStatus.ACCEPTED
        ):
            return self._blocked(artifact, "Core did not accept this expression")
        if not self._normal_dialogue_output_enabled:
            return self._blocked(
                artifact,
                "protection boundary disabled normal dialogue output",
            )
        if artifact.mode is ExpressionMode.SILENCE:
            return DialogueOutput(
                lifecycle_id=artifact.lifecycle_id,
                artifact_id=artifact.artifact_id,
                status=OutputReleaseStatus.SILENT,
                text=None,
                reason="finalized silence is preserved at the output boundary",
            )
        return DialogueOutput(
            lifecycle_id=artifact.lifecycle_id,
            artifact_id=artifact.artifact_id,
            status=OutputReleaseStatus.RELEASED,
            text=artifact.text,
            reason="accepted expression passed the direct-dialogue boundary",
        )

    @staticmethod
    def _blocked(
        artifact: ExpressionArtifact,
        reason: str,
    ) -> DialogueOutput:
        return DialogueOutput(
            lifecycle_id=artifact.lifecycle_id,
            artifact_id=artifact.artifact_id,
            status=OutputReleaseStatus.BLOCKED,
            text=None,
            reason=reason,
        )
