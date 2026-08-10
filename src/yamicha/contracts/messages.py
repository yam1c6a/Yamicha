"""Common information carried by requests and results."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping


class VerificationState(StrEnum):
    """Technical verification state; this is not a semantic judgment."""

    UNKNOWN = "unknown"
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class MessageEnvelope:
    """Minimum traceable envelope shared by responsibility boundaries."""

    message_id: str
    correlation_id: str
    occurred_at: datetime
    received_at: datetime
    source: str
    type: str
    payload: Mapping[str, object]
    schema_version: str
    verification: VerificationState = VerificationState.UNKNOWN

    def __post_init__(self) -> None:
        required = {
            "message_id": self.message_id,
            "correlation_id": self.correlation_id,
            "source": self.source,
            "type": self.type,
            "schema_version": self.schema_version,
        }
        empty = [name for name, value in required.items() if not value.strip()]
        if empty:
            raise ValueError(f"required message fields must not be empty: {empty}")
        if self.occurred_at.tzinfo is None or self.received_at.tzinfo is None:
            raise ValueError("message timestamps must include timezone information")
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))
