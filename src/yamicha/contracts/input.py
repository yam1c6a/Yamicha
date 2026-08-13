"""Contracts for external text reception and sensory normalization."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .time import ExternalTime


class SourceVerification(StrEnum):
    VERIFIED = "verified"
    UNVERIFIABLE = "unverifiable"


class InputDisposition(StrEnum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    INVALID_FORMAT = "invalid_format"
    UNTRUSTED = "untrusted"
    UNSUPPORTED = "unsupported"
    UNAUTHORIZED = "unauthorized"
    BLOCKED = "blocked"


class InputQuality(StrEnum):
    VALID = "valid"
    DUPLICATE = "duplicate"


class ContentTrust(StrEnum):
    """External content remains semantically untrusted after format checks."""

    UNTRUSTED = "untrusted"


@dataclass(frozen=True, slots=True)
class RawTextInput:
    """Unvalidated input at the outside edge of the body boundary."""

    input_id: str
    received_at: ExternalTime
    source_id: str
    content: object
    media_type: str = "text/plain"
    schema_version: str = "1"
    source_verification: SourceVerification = SourceVerification.UNVERIFIABLE


@dataclass(frozen=True, slots=True)
class ValidatedTextInput:
    """Structurally verified input allowed to reach Sensation."""

    input_id: str
    received_at: ExternalTime
    source_id: str
    content: str
    media_type: str
    schema_version: str
    source_verification: SourceVerification
    boundary_verified: bool

    def __post_init__(self) -> None:
        if not self.boundary_verified:
            raise ValueError("validated input requires boundary verification")


@dataclass(frozen=True, slots=True)
class InputRejection:
    input_id: str
    disposition: InputDisposition
    reason: str

    def __post_init__(self) -> None:
        if self.disposition in {
            InputDisposition.ACCEPTED,
            InputDisposition.DUPLICATE,
        }:
            raise ValueError("input rejection requires a rejection disposition")
        if not self.reason.strip():
            raise ValueError("input rejection reason must not be empty")


@dataclass(frozen=True, slots=True)
class RawReception:
    """Original direct reception owned by Sensation, not by Memory."""

    reception_id: str
    input_id: str
    received_at: ExternalTime
    source_id: str
    original_text: str
    media_type: str
    quality: InputQuality


class SensoryEventKind(StrEnum):
    TEXT_MESSAGE = "text_message"


@dataclass(frozen=True, slots=True)
class EventMeaning:
    """Minimal literal interpretation, distinct from the raw reception."""

    kind: SensoryEventKind
    normalized_text: str


@dataclass(frozen=True, slots=True)
class SensoryEvent:
    """Normalized event that Sensation may notify only to Core."""

    event_id: str
    correlation_id: str
    raw_reference: str
    received_at: ExternalTime
    source_id: str
    quality: InputQuality
    content_trust: ContentTrust
    meaning: EventMeaning

    def __post_init__(self) -> None:
        identifiers = {
            "event_id": self.event_id,
            "correlation_id": self.correlation_id,
            "raw_reference": self.raw_reference,
            "source_id": self.source_id,
        }
        empty = [name for name, value in identifiers.items() if not value.strip()]
        if empty:
            raise ValueError(f"sensory event identifiers must not be empty: {empty}")
