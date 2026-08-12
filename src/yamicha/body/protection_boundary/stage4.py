"""Boundary material supplied to stage-4 judgment."""

from __future__ import annotations

from yamicha.contracts import BoundaryDecisionMaterial, SensoryEvent

from .stage3 import Stage3ProtectionBoundary


class Stage4ProtectionBoundary(Stage3ProtectionBoundary):
    """Expose validated boundary facts without interpreting user intent."""

    def present_decision_material(
        self,
        event: SensoryEvent,
    ) -> BoundaryDecisionMaterial:
        return BoundaryDecisionMaterial(
            lifecycle_id=event.correlation_id,
            version="protection-boundary:1",
            input_validated=True,
            content_trust=event.content_trust,
            external_effects_permitted=False,
        )
