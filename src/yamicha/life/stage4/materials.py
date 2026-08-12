"""Stage-4 judgment materials owned by State, Memory, and Relationship."""

from __future__ import annotations

from yamicha.contracts import (
    InternalEvent,
    MemoryDecisionMaterial,
    RelationshipDecisionMaterial,
    SensoryEvent,
    StateDecisionMaterial,
    StateSnapshot,
)
from yamicha.life.stage3 import Stage3Memory, Stage3Relationship, Stage3State


class Stage4State(Stage3State):
    def __init__(self) -> None:
        super().__init__()
        self._material_version = 0

    def observe_internal_event(self, event: InternalEvent) -> StateSnapshot:
        snapshot = super().observe_internal_event(event)
        self._material_version += 1
        return snapshot

    def present_decision_material(
        self,
        event: SensoryEvent,
    ) -> StateDecisionMaterial:
        return StateDecisionMaterial(
            lifecycle_id=event.correlation_id,
            version=f"state:{self._material_version}",
            available=self._material_version > 0,
            operating_state=self.operating_state,
            constraints=(),
        )


class Stage4Memory(Stage3Memory):
    def __init__(self, *, available: bool = True) -> None:
        self._available = available

    def present_decision_material(
        self,
        event: SensoryEvent,
    ) -> MemoryDecisionMaterial:
        uncertainties = () if self._available else ("memory material is unavailable",)
        return MemoryDecisionMaterial(
            lifecycle_id=event.correlation_id,
            version="memory:1",
            available=self._available,
            related_references=(),
            uncertainties=uncertainties,
        )


class Stage4Relationship(Stage3Relationship):
    _BOUNDARY_MARKERS = (
        "境界を無視",
        "許可なく",
        "秘密を公開",
    )

    def __init__(self, *, known_counterpart_id: str = "human-001") -> None:
        if not known_counterpart_id.strip():
            raise ValueError("known counterpart ID must not be empty")
        self._known_counterpart_id = known_counterpart_id

    def present_decision_material(
        self,
        event: SensoryEvent,
    ) -> RelationshipDecisionMaterial:
        text = event.meaning.normalized_text
        boundary_reasons = tuple(
            f"relationship boundary marker detected: {marker}"
            for marker in self._BOUNDARY_MARKERS
            if marker in text
        )
        counterpart_known = event.source_id == self._known_counterpart_id
        return RelationshipDecisionMaterial(
            lifecycle_id=event.correlation_id,
            version="relationship:1",
            available=True,
            counterpart_id=event.source_id,
            counterpart_known=counterpart_known,
            boundary_violation=bool(boundary_reasons),
            boundary_reasons=boundary_reasons,
            current_consent=None,
        )
