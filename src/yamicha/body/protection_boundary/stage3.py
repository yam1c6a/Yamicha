"""Stage-3 validation boundary for the first text input channel."""

from __future__ import annotations

from yamicha.contracts import (
    InputDisposition,
    InputRejection,
    ExternalTime,
    MessageEnvelope,
    RawTextInput,
    SourceVerification,
    UnimplementedResponsibilityError,
    ValidatedTextInput,
)

from .port import PROTECTION_BOUNDARY_DEFINITION


class Stage3ProtectionBoundary:
    definition = PROTECTION_BOUNDARY_DEFINITION

    def __init__(self, *, max_text_length: int = 4096) -> None:
        if max_text_length < 1:
            raise ValueError("max_text_length must be positive")
        self._max_text_length = max_text_length
        self._validation_count = 0

    @property
    def validation_count(self) -> int:
        return self._validation_count

    def validate(
        self,
        raw: RawTextInput,
    ) -> ValidatedTextInput | InputRejection:
        self._validation_count += 1
        input_id = raw.input_id if isinstance(raw.input_id, str) else "<invalid>"
        if (
            not isinstance(raw.input_id, str)
            or not raw.input_id.strip()
            or not isinstance(raw.source_id, str)
            or not raw.source_id.strip()
            or not isinstance(raw.received_at, ExternalTime)
            or not isinstance(raw.content, str)
            or not raw.content.strip()
            or len(raw.content) > self._max_text_length
        ):
            return InputRejection(
                input_id=input_id,
                disposition=InputDisposition.INVALID_FORMAT,
                reason="required text input fields are missing or malformed",
            )
        if raw.media_type != "text/plain" or raw.schema_version != "1":
            return InputRejection(
                input_id=input_id,
                disposition=InputDisposition.UNSUPPORTED,
                reason="input media type or schema version is unsupported",
            )
        if raw.source_verification is not SourceVerification.VERIFIED:
            return InputRejection(
                input_id=input_id,
                disposition=InputDisposition.UNTRUSTED,
                reason="input source could not be technically verified",
            )
        return ValidatedTextInput(
            input_id=raw.input_id,
            received_at=raw.received_at,
            source_id=raw.source_id,
            content=raw.content,
            media_type=raw.media_type,
            schema_version=raw.schema_version,
            source_verification=raw.source_verification,
            boundary_verified=True,
        )

    def handle(self, message: MessageEnvelope) -> MessageEnvelope:
        raise UnimplementedResponsibilityError(
            "generic protection message handling starts after stage 3"
        )
