from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from yamicha.contracts import MessageEnvelope, VerificationState  # noqa: E402


class MessageEnvelopeTest(unittest.TestCase):
    def test_envelope_keeps_all_required_trace_fields(self) -> None:
        timestamp = datetime(2026, 8, 10, tzinfo=UTC)
        envelope = MessageEnvelope(
            message_id="message-001",
            correlation_id="cycle-001",
            occurred_at=timestamp,
            received_at=timestamp,
            source="test",
            type="test.event",
            payload={"text": "こんにちは"},
            schema_version="1",
            verification=VerificationState.VERIFIED,
        )

        self.assertEqual(envelope.payload["text"], "こんにちは")
        self.assertEqual(envelope.verification, VerificationState.VERIFIED)
        with self.assertRaises(TypeError):
            envelope.payload["text"] = "変更"  # type: ignore[index]

    def test_timezone_is_required(self) -> None:
        timestamp = datetime(2026, 8, 10)
        with self.assertRaises(ValueError):
            MessageEnvelope(
                message_id="message-001",
                correlation_id="cycle-001",
                occurred_at=timestamp,
                received_at=timestamp,
                source="test",
                type="test.event",
                payload={},
                schema_version="1",
            )

    def test_empty_identifier_is_rejected(self) -> None:
        timestamp = datetime(2026, 8, 10, tzinfo=UTC)
        with self.assertRaises(ValueError):
            MessageEnvelope(
                message_id="",
                correlation_id="cycle-001",
                occurred_at=timestamp,
                received_at=timestamp,
                source="test",
                type="test.event",
                payload={},
                schema_version="1",
            )
