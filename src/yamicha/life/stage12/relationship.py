"""Relationship-owned dialogue continuity for stage 12."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from uuid import uuid4

from yamicha.contracts import (
    ContentTrust,
    DialogueContext,
    DialogueContextStatus,
    DialogueContextWindow,
    DialogueOutput,
    DialogueSpeaker,
    DialogueTurn,
    ExternalTime,
    OutputReleaseStatus,
    RelationshipDecisionMaterial,
    RelationshipPersistenceSnapshot,
    SensoryEvent,
)
from yamicha.life.stage7 import Stage7Relationship


class Stage12Relationship(Stage7Relationship):
    def __init__(
        self,
        *,
        known_counterpart_id: str = "human-001",
        context_id_factory: Callable[[], str] | None = None,
        turn_id_factory: Callable[[], str] | None = None,
    ) -> None:
        super().__init__(known_counterpart_id=known_counterpart_id)
        self._context_id_factory = context_id_factory or (lambda: str(uuid4()))
        self._turn_id_factory = turn_id_factory or (lambda: str(uuid4()))
        self._active_context: DialogueContext | None = None
        self._context_ids: set[str] = set()
        self._retired_context_ids: set[str] = set()
        self._turn_ids: set[str] = set()
        self._relationship_version = 1

    @property
    def active_dialogue_context(self) -> DialogueContext | None:
        return self._active_context

    def ensure_active_context(self, event: SensoryEvent) -> DialogueContext:
        if event.source_id != self._known_counterpart_id:
            raise ValueError("dialogue context requires the known counterpart")
        if self._active_context is None:
            self._active_context = self._new_context(event.received_at, None)
            self._relationship_version += 1
        return self._active_context

    def start_new_context(self, started_at: ExternalTime) -> DialogueContext:
        if self._active_context is not None:
            if started_at.value < self._active_context.updated_at.value:
                raise ValueError("new dialogue context cannot precede the active context")
            previous_context_id = self._active_context.context_id
            self._retired_context_ids.add(previous_context_id)
        else:
            previous_context_id = None
        self._active_context = self._new_context(started_at, previous_context_id)
        self._relationship_version += 1
        return self._active_context

    def record_completed_exchange(
        self,
        event: SensoryEvent,
        output: DialogueOutput,
    ) -> DialogueContext:
        context = self.ensure_active_context(event)
        if output.lifecycle_id != event.correlation_id:
            raise ValueError("dialogue exchange must belong to one lifecycle")
        if event.received_at.value < context.updated_at.value:
            raise ValueError("dialogue exchange cannot precede the active context")
        turns = [
            DialogueTurn(
                turn_id=self._next_turn_id(),
                context_id=context.context_id,
                lifecycle_id=event.correlation_id,
                speaker=DialogueSpeaker.HUMAN,
                text=event.meaning.normalized_text,
                source_reference=event.event_id,
                occurred_at=event.received_at,
                content_trust=ContentTrust.UNTRUSTED,
            )
        ]
        if output.status is OutputReleaseStatus.RELEASED:
            assert output.text is not None
            turns.append(
                DialogueTurn(
                    turn_id=self._next_turn_id(),
                    context_id=context.context_id,
                    lifecycle_id=event.correlation_id,
                    speaker=DialogueSpeaker.YAMICHA,
                    text=output.text,
                    source_reference=output.artifact_id,
                    occurred_at=event.received_at,
                    content_trust=None,
                )
            )
        self._active_context = replace(
            context,
            turns=(*context.turns, *turns),
            version=context.version + 1,
            updated_at=event.received_at,
        )
        self._relationship_version += 1
        return self._active_context

    def select_dialogue_window(
        self,
        event: SensoryEvent,
        *,
        max_exchanges: int,
        max_characters: int,
    ) -> DialogueContextWindow:
        context = self.ensure_active_context(event)
        current_characters = len(event.meaning.normalized_text)
        if current_characters > max_characters:
            raise ValueError("current input exceeds the dialogue context limit")
        exchanges: list[list[DialogueTurn]] = []
        for turn in context.turns:
            if not exchanges or exchanges[-1][0].lifecycle_id != turn.lifecycle_id:
                exchanges.append([turn])
            else:
                exchanges[-1].append(turn)
        remaining = max_characters - current_characters
        selected: list[list[DialogueTurn]] = []
        for exchange in reversed(exchanges[-max_exchanges:]):
            characters = sum(len(turn.text) for turn in exchange)
            if characters > remaining:
                break
            selected.append(exchange)
            remaining -= characters
        turns = tuple(
            turn
            for exchange in reversed(selected)
            for turn in exchange
        )
        return DialogueContextWindow(
            context_id=context.context_id,
            context_version=context.version,
            turns=turns,
            current_input_reference=event.event_id,
            current_input_characters=current_characters,
            max_exchanges=max_exchanges,
            max_characters=max_characters,
        )

    def present_decision_material(
        self,
        event: SensoryEvent,
    ) -> RelationshipDecisionMaterial:
        context = self.ensure_active_context(event)
        base = super().present_decision_material(event)
        return replace(
            base,
            version=f"relationship:{self._relationship_version}",
            dialogue_context_id=context.context_id,
            dialogue_context_version=context.version,
        )

    def persistence_snapshot(self) -> RelationshipPersistenceSnapshot:
        return RelationshipPersistenceSnapshot(
            known_counterpart_id=self._known_counterpart_id,
            version=self._relationship_version,
            active_dialogue_context=self._active_context,
            retired_dialogue_context_ids=tuple(sorted(self._retired_context_ids)),
        )

    def restore_owned_state(
        self,
        snapshot: RelationshipPersistenceSnapshot,
    ) -> None:
        if (
            self._active_context is not None
            or self._context_ids
            or self._retired_context_ids
            or self._turn_ids
        ):
            raise RuntimeError("Relationship can only restore into a fresh owner")
        self._known_counterpart_id = snapshot.known_counterpart_id
        self._relationship_version = snapshot.version
        self._retired_context_ids.update(snapshot.retired_dialogue_context_ids)
        self._context_ids.update(snapshot.retired_dialogue_context_ids)
        context = snapshot.active_dialogue_context
        if context is not None:
            self._active_context = context
            self._context_ids.add(context.context_id)
            self._turn_ids.update(turn.turn_id for turn in context.turns)

    def _new_context(
        self,
        started_at: ExternalTime,
        previous_context_id: str | None,
    ) -> DialogueContext:
        context_id = self._required_unique_id(
            self._context_id_factory,
            self._context_ids,
            "dialogue context",
        )
        return DialogueContext(
            context_id=context_id,
            counterpart_id=self._known_counterpart_id,
            status=DialogueContextStatus.ACTIVE,
            turns=(),
            version=1,
            started_at=started_at,
            updated_at=started_at,
            previous_context_id=previous_context_id,
        )

    def _next_turn_id(self) -> str:
        return self._required_unique_id(
            self._turn_id_factory,
            self._turn_ids,
            "dialogue turn",
        )

    @staticmethod
    def _required_unique_id(
        factory: Callable[[], str],
        used: set[str],
        label: str,
    ) -> str:
        value = factory()
        if not value.strip() or value in used:
            raise ValueError(f"{label} ID must be non-empty and unique")
        used.add(value)
        return value
