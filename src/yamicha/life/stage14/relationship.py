"""Relationship-owned redaction of deletable active dialogue content."""

from __future__ import annotations

from dataclasses import replace

from yamicha.life.stage12 import Stage12Relationship


REDACTED_DIALOGUE_TEXT = "[物理削除済み]"


class Stage14Relationship(Stage12Relationship):
    def redact_active_dialogue(
        self,
        *,
        source_references: tuple[str, ...],
        lifecycle_ids: tuple[str, ...],
    ) -> int:
        context = self._active_context
        if context is None:
            return 0
        references = set(source_references)
        lifecycles = set(lifecycle_ids)
        redacted = 0
        turns = []
        for turn in context.turns:
            if (
                turn.source_reference in references
                or turn.lifecycle_id in lifecycles
            ) and turn.text != REDACTED_DIALOGUE_TEXT:
                turns.append(replace(turn, text=REDACTED_DIALOGUE_TEXT))
                redacted += 1
            else:
                turns.append(turn)
        if redacted:
            self._active_context = replace(
                context,
                turns=tuple(turns),
                version=context.version + 1,
            )
            self._relationship_version += 1
        return redacted
