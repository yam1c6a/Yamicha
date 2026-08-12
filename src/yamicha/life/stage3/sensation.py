"""Sensation's stage-3 raw reception and literal normalization."""

from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from yamicha.contracts import (
    ContentTrust,
    EventMeaning,
    ExecutionOpportunity,
    InputQuality,
    InternalEvent,
    MessageEnvelope,
    RawReception,
    SensoryEvent,
    SensoryEventKind,
    SourceVerification,
    UnimplementedResponsibilityError,
    ValidatedTextInput,
)
from yamicha.life.ports import SENSATION_DEFINITION


class Stage3Sensation:
    definition = SENSATION_DEFINITION

    def __init__(
        self,
        *,
        reception_id_factory: Callable[[], str] | None = None,
        event_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._reception_id_factory = reception_id_factory or (lambda: str(uuid4()))
        self._event_id_factory = event_id_factory or (lambda: str(uuid4()))
        self._seen_input_ids: set[str] = set()
        self._receptions: list[RawReception] = []

    @property
    def reception_count(self) -> int:
        return len(self._receptions)

    @property
    def receptions(self) -> tuple[RawReception, ...]:
        return tuple(self._receptions)

    def receive_execution_opportunity(
        self,
        opportunity: ExecutionOpportunity,
        correlation_id: str,
    ) -> InternalEvent:
        return InternalEvent(
            correlation_id=correlation_id,
            source="runtime",
            opportunity=opportunity,
        )

    def receive_validated_text(
        self,
        validated: ValidatedTextInput,
        correlation_id: str,
    ) -> SensoryEvent:
        if not validated.boundary_verified:
            raise ValueError("unverified input must not reach Sensation")
        if validated.source_verification is not SourceVerification.VERIFIED:
            raise ValueError("unverified source must not reach Sensation")
        quality = (
            InputQuality.DUPLICATE
            if validated.input_id in self._seen_input_ids
            else InputQuality.VALID
        )
        self._seen_input_ids.add(validated.input_id)
        reception_id = self._required_id(self._reception_id_factory, "reception")
        event_id = self._required_id(self._event_id_factory, "event")
        reception = RawReception(
            reception_id=reception_id,
            input_id=validated.input_id,
            received_at=validated.received_at,
            source_id=validated.source_id,
            original_text=validated.content,
            media_type=validated.media_type,
            quality=quality,
        )
        self._receptions.append(reception)
        normalized_text = validated.content.replace("\r\n", "\n").replace("\r", "\n")
        return SensoryEvent(
            event_id=event_id,
            correlation_id=correlation_id,
            raw_reference=reception_id,
            received_at=validated.received_at,
            source_id=validated.source_id,
            quality=quality,
            content_trust=ContentTrust.UNTRUSTED,
            meaning=EventMeaning(
                kind=SensoryEventKind.TEXT_MESSAGE,
                normalized_text=normalized_text,
            ),
        )

    def handle(self, message: MessageEnvelope) -> MessageEnvelope:
        raise UnimplementedResponsibilityError(
            "generic sensation message handling starts after stage 3"
        )

    @staticmethod
    def _required_id(factory: Callable[[], str], label: str) -> str:
        value = factory()
        if not value.strip():
            raise ValueError(f"{label} id factory returned an empty identifier")
        return value
