"""Persistence view of the stage-7 protection boundary state."""

from __future__ import annotations

from yamicha.contracts import ProtectionPersistenceSnapshot

from .stage5 import Stage5ProtectionBoundary


class Stage7ProtectionBoundary(Stage5ProtectionBoundary):
    def persistence_snapshot(self) -> ProtectionPersistenceSnapshot:
        return ProtectionPersistenceSnapshot(
            normal_dialogue_output_enabled=self._normal_dialogue_output_enabled,
        )

    def restore_owned_state(
        self,
        snapshot: ProtectionPersistenceSnapshot,
    ) -> None:
        self._normal_dialogue_output_enabled = (
            snapshot.normal_dialogue_output_enabled
        )
