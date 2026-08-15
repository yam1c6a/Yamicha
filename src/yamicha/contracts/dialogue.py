"""Stage-12 contracts for bounded, relationship-owned dialogue context."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .input import ContentTrust
from .time import ExternalTime


class DialogueSpeaker(StrEnum):
    HUMAN = "human"
    YAMICHA = "yamicha"


class DialogueContextStatus(StrEnum):
    ACTIVE = "active"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class DialogueTurn:
    turn_id: str
    context_id: str
    lifecycle_id: str
    speaker: DialogueSpeaker
    text: str
    source_reference: str
    occurred_at: ExternalTime
    verified: bool = True
    content_trust: ContentTrust | None = None

    def __post_init__(self) -> None:
        required = (
            self.turn_id,
            self.context_id,
            self.lifecycle_id,
            self.text,
            self.source_reference,
        )
        if not all(value.strip() for value in required):
            raise ValueError("dialogue turn values must not be empty")
        if not self.verified:
            raise ValueError("dialogue context may contain only verified turns")
        if self.speaker is DialogueSpeaker.HUMAN:
            if self.content_trust is not ContentTrust.UNTRUSTED:
                raise ValueError("human dialogue text must remain semantically untrusted")
        elif self.content_trust is not None:
            raise ValueError("Yamicha dialogue output does not use external content trust")


@dataclass(frozen=True, slots=True)
class DialogueContext:
    context_id: str
    counterpart_id: str
    status: DialogueContextStatus
    turns: tuple[DialogueTurn, ...]
    version: int
    started_at: ExternalTime
    updated_at: ExternalTime
    closed_at: ExternalTime | None = None
    previous_context_id: str | None = None

    def __post_init__(self) -> None:
        if not self.context_id.strip() or not self.counterpart_id.strip():
            raise ValueError("dialogue context identifiers must not be empty")
        if self.version < 1:
            raise ValueError("dialogue context version must be positive")
        if self.previous_context_id is not None and (
            not self.previous_context_id.strip()
            or self.previous_context_id == self.context_id
        ):
            raise ValueError("previous dialogue context reference is invalid")
        if self.updated_at.value < self.started_at.value:
            raise ValueError("dialogue context update cannot precede its start")
        if (self.status is DialogueContextStatus.CLOSED) != (
            self.closed_at is not None
        ):
            raise ValueError("only a closed dialogue context has a close time")
        if self.closed_at is not None and self.closed_at.value < self.updated_at.value:
            raise ValueError("dialogue context close cannot precede its last update")
        if any(turn.context_id != self.context_id for turn in self.turns):
            raise ValueError("dialogue turns must belong to their context")
        turn_ids = tuple(turn.turn_id for turn in self.turns)
        if len(set(turn_ids)) != len(turn_ids):
            raise ValueError("dialogue turn IDs must be unique")
        occurred = tuple(turn.occurred_at.value for turn in self.turns)
        if occurred != tuple(sorted(occurred)):
            raise ValueError("dialogue turns must be chronological")
        if any(
            value < self.started_at.value or value > self.updated_at.value
            for value in occurred
        ):
            raise ValueError("dialogue turn time is outside its context")
        lifecycle_ids = tuple(turn.lifecycle_id for turn in self.turns)
        seen_lifecycles: set[str] = set()
        previous_lifecycle: str | None = None
        for lifecycle_id in lifecycle_ids:
            if lifecycle_id != previous_lifecycle:
                if lifecycle_id in seen_lifecycles:
                    raise ValueError("dialogue lifecycle turns must remain contiguous")
                seen_lifecycles.add(lifecycle_id)
                previous_lifecycle = lifecycle_id
        for lifecycle_id in dict.fromkeys(lifecycle_ids):
            exchange = tuple(
                turn for turn in self.turns if turn.lifecycle_id == lifecycle_id
            )
            if (
                len(exchange) not in {1, 2}
                or exchange[0].speaker is not DialogueSpeaker.HUMAN
                or (
                    len(exchange) == 2
                    and exchange[1].speaker is not DialogueSpeaker.YAMICHA
                )
            ):
                raise ValueError("dialogue lifecycle has an invalid speaker sequence")


@dataclass(frozen=True, slots=True)
class DialogueContextWindow:
    """A deterministic suffix of prior turns approved for one LLM request."""

    context_id: str
    context_version: int
    turns: tuple[DialogueTurn, ...]
    current_input_reference: str
    current_input_characters: int
    max_exchanges: int
    max_characters: int

    def __post_init__(self) -> None:
        if not self.context_id.strip() or not self.current_input_reference.strip():
            raise ValueError("dialogue context window identifiers must not be empty")
        if (
            self.context_version < 1
            or self.current_input_characters <= 0
            or self.max_exchanges <= 0
            or self.max_characters <= 0
        ):
            raise ValueError("dialogue context window limits must be positive")
        if any(turn.context_id != self.context_id for turn in self.turns):
            raise ValueError("dialogue context window contains a foreign turn")
        turn_ids = tuple(turn.turn_id for turn in self.turns)
        if len(set(turn_ids)) != len(turn_ids):
            raise ValueError("dialogue context window contains duplicate turns")
        occurred = tuple(turn.occurred_at.value for turn in self.turns)
        if occurred != tuple(sorted(occurred)):
            raise ValueError("dialogue context window must be chronological")
        lifecycle_ids = tuple(dict.fromkeys(turn.lifecycle_id for turn in self.turns))
        if len(lifecycle_ids) > self.max_exchanges:
            raise ValueError("dialogue context window exceeds its exchange limit")
        if self.total_characters > self.max_characters:
            raise ValueError("dialogue context window exceeds its character limit")

    @property
    def total_characters(self) -> int:
        return self.current_input_characters + sum(
            len(turn.text) for turn in self.turns
        )
