from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from yamicha.body.persistence import PersistenceConsistencyError  # noqa: E402
from yamicha.bootstrap import (  # noqa: E402
    make_stage13_system,
    make_stage15_system,
    run_interactive_console,
)
from yamicha.contracts import (  # noqa: E402
    ClockObservation,
    ExternalIntelligenceResponse,
    ExternalTime,
    InputDisposition,
    IntelligenceResultStatus,
    MonotonicTime,
    ProtectionMode,
    RawTextInput,
    SourceVerification,
)


NOW = ExternalTime(datetime(2026, 8, 16, 12, 0, tzinfo=UTC))


class FixedClock:
    def observe(self) -> ClockObservation:
        return ClockObservation(external=NOW, monotonic=MonotonicTime(100.0))


class FakeTransport:
    def generate(self, request):
        return ExternalIntelligenceResponse(
            status=IntelligenceResultStatus.SUCCESS,
            model=request.proposal.model,
            content=json.dumps({"reply": "応答"}, ensure_ascii=False),
            detail="stage-15 upgrade test transport",
        )


class Stage15UpgradeCompatibilityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "yamicha.sqlite3"
        self.systems = []
        self.sequence = 0

    def tearDown(self) -> None:
        for system in reversed(self.systems):
            if not system._closed:
                system.shutdown()
        self.directory.cleanup()

    def make_system(self, factory=make_stage15_system, **options):
        system = factory(
            persistence_path=self.path,
            clock=FixedClock(),
            persistence_time_factory=lambda: NOW,
            intelligence_transport=FakeTransport(),
            **options,
        )
        self.systems.append(system)
        return system

    def send(self, system, content: str, *, source_id: str = "human-001"):
        self.sequence += 1
        return system.receive_text(RawTextInput(
            input_id=f"upgrade-input-{self.sequence}",
            received_at=ExternalTime(NOW.value + timedelta(seconds=self.sequence)),
            source_id=source_id,
            content=content,
            source_verification=SourceVerification.VERIFIED,
        ))

    def test_stage13_upgrades_directly_and_retains_state_across_another_restart(self) -> None:
        previous = self.make_system(make_stage13_system)
        self.send(previous, "覚えて：猫が好き")
        identity = previous.recovery.identity.subject_id
        memories = previous.memory.conversation_memory_items
        context = previous.relationship.active_dialogue_context
        records = previous.core.lifecycle_records
        previous.shutdown()

        upgraded = self.make_system(require_existing_persistence=True)

        self.assertEqual(upgraded.recovery.identity.subject_id, identity)
        self.assertEqual(upgraded.recovery.identity.configuration_version, "stage15-v1")
        self.assertEqual(upgraded.memory.conversation_memory_items, memories)
        self.assertEqual(upgraded.relationship.active_dialogue_context, context)
        self.assertEqual(upgraded.core.lifecycle_records, records)
        self.assertEqual(upgraded.relationship.relationship_items, ())
        self.assertEqual(upgraded.relationship.relationship_changes, ())
        self.assertEqual(upgraded.relationship.relationship_pending_confirmations, ())
        self.assertEqual(upgraded.relationship.relationship_usages, ())
        self.send(upgraded, "関係：希望：説明は短めに")
        self.send(upgraded, "関係確認")
        relationship = upgraded.relationship.persistence_snapshot()
        upgraded.shutdown()

        restored = self.make_system(require_existing_persistence=True)

        self.assertEqual(restored.recovery.identity.subject_id, identity)
        self.assertEqual(restored.memory.conversation_memory_items, memories)
        self.assertEqual(restored.relationship.persistence_snapshot(), relationship)

    def test_default_console_opens_stage13_database_without_intermediate_startup(self) -> None:
        previous = self.make_system(
            make_stage13_system,
            known_counterpart_id="local-console",
            capability_authority_id="local-console",
        )
        self.send(previous, "覚えて：コーヒーが好き", source_id="local-console")
        identity = previous.recovery.identity.subject_id
        memories = previous.memory.conversation_memory_items
        context = previous.relationship.active_dialogue_context
        previous.shutdown()
        output = StringIO()

        exit_code = run_interactive_console(
            input_stream=StringIO("/memory\n/relationship\n/quit\n"),
            output_stream=output,
            persistence_path=self.path,
        )

        self.assertEqual(exit_code, 0)
        self.assertIn("Yamicha 対話コンソール（段階15）", output.getvalue())
        self.assertIn("コーヒーが好き", output.getvalue())
        restored = self.make_system(require_existing_persistence=True)
        self.assertEqual(restored.recovery.identity.subject_id, identity)
        self.assertEqual(restored.memory.conversation_memory_items, memories)
        self.assertEqual(restored.relationship.active_dialogue_context, context)
        self.assertEqual(restored.relationship.relationship_items, ())

    def test_stage13_protection_remains_active_after_direct_upgrade(self) -> None:
        previous = self.make_system(make_stage13_system)
        self.send(previous, "覚えて：猫が好き")
        memories = previous.memory.conversation_memory_items
        previous.activate_fixed_protection(NOW)
        previous.shutdown()

        upgraded = self.make_system(require_existing_persistence=True)

        self.assertEqual(upgraded.protection_boundary.mode, ProtectionMode.PROTECTED)
        self.assertEqual(upgraded.memory.conversation_memory_items, memories)
        self.assertEqual(self.send(upgraded, "こんにちは").disposition, InputDisposition.BLOCKED)

    def test_unsupported_configuration_is_still_rejected_without_rewriting_identity(self) -> None:
        previous = self.make_system(
            make_stage13_system,
            _configuration_version="unsupported-future-v1",
        )
        self.send(previous, "覚えて：猫が好き")
        identity = previous.recovery.identity.subject_id
        previous.shutdown()

        with self.assertRaises(PersistenceConsistencyError):
            self.make_system(require_existing_persistence=True)

        connection = sqlite3.connect(self.path)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT subject_id, configuration_version FROM identity WHERE singleton = 1"
                ).fetchone(),
                (identity, "unsupported-future-v1"),
            )
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
