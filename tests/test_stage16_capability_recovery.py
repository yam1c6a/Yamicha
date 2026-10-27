from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from yamicha.body.persistence import PersistenceCommitError  # noqa: E402
from yamicha.bootstrap import make_stage16_system  # noqa: E402
from yamicha.contracts import (  # noqa: E402
    CapabilityResultStatus,
    ClockObservation,
    ExternalIntelligenceResponse,
    ExternalTime,
    IntelligenceResultStatus,
    MonotonicTime,
    RawTextInput,
    ReadOnlyToolResult,
    SourceVerification,
)


NOW = ExternalTime(datetime(2026, 10, 27, 12, tzinfo=UTC))


class FixedClock:
    def observe(self) -> ClockObservation:
        return ClockObservation(external=NOW, monotonic=MonotonicTime(100.0))


class UnavailableTransport:
    def generate(self, request):
        return ExternalIntelligenceResponse(
            status=IntelligenceResultStatus.UNAVAILABLE,
            model=request.proposal.model,
            content=None,
            detail="deterministic recovery test",
        )


class CountingReader:
    def __init__(self) -> None:
        self.calls = 0

    def read(self, target: str) -> ReadOnlyToolResult:
        self.calls += 1
        return ReadOnlyToolResult(
            status=CapabilityResultStatus.SUCCESS,
            content="observed content",
            observed_scope=target,
            remaining_scope=None,
            detail="test read completed",
            uncertainty=None,
        )


class Stage16CapabilityRecoveryTest(unittest.TestCase):
    def test_failed_result_persistence_blocks_further_dialogue_and_capability(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            reader = CountingReader()
            system = make_stage16_system(
                persistence_path=Path(directory) / "yamicha.sqlite3",
                clock=FixedClock(),
                persistence_time_factory=lambda: NOW,
                intelligence_transport=UnavailableTransport(),
                capability_reader=reader,
                capability_authority_id="human-001",
                authorized_read_targets=("document.txt",),
            )
            try:
                def send(input_id: str):
                    return system.receive_text(RawTextInput(
                        input_id=input_id, received_at=NOW, source_id="human-001",
                        content="こんにちは", source_verification=SourceVerification.VERIFIED,
                    ))

                send("initial")
                previous_evidence = system.memory.experience_evidence
                with patch.object(
                    system.persistence, "complete_capability_execution",
                    side_effect=PersistenceCommitError("simulated result write failure"),
                ):
                    with self.assertRaises(PersistenceCommitError):
                        system.use_read_capability(
                            target="document.txt", authority_id="human-001",
                            idempotency_key="failed-read", requested_at=NOW,
                        )

                self.assertEqual(reader.calls, 1)
                self.assertFalse(system.text_input_runner.persistence_healthy)
                self.assertEqual(system.memory.experience_evidence, previous_evidence)
                self.assertEqual(system.persistence.capability_execution_records()[-1].status, "reserved")
                with self.assertRaisesRegex(RuntimeError, "persistence is unavailable"):
                    send("after-failure")
                with self.assertRaisesRegex(RuntimeError, "persistence is unavailable"):
                    system.use_read_capability(
                        target="document.txt", authority_id="human-001",
                        idempotency_key="next-read", requested_at=NOW,
                    )
                self.assertEqual(reader.calls, 1)
            finally:
                system.abandon()


if __name__ == "__main__":
    unittest.main()
