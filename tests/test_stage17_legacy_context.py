"""G17 compatibility for stage16-v1 snapshots without material manifests."""

from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import test_stage16_experience_dialogue as fixtures
from yamicha.bootstrap import make_stage16_system
from yamicha.contracts import ConversationMemoryOperationStatus, ExternalTime
from yamicha.life.stage14.relationship import REDACTED_DIALOGUE_TEXT


class Stage17LegacyContextTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "legacy.sqlite3"
        self.systems = []
        self.sequence = 0

    def tearDown(self) -> None:
        for system in reversed(self.systems):
            if not system._closed:
                if system.text_input_runner.persistence_healthy:
                    system.shutdown()
                else:
                    system.abandon()
        self.directory.cleanup()

    def make_system(self, transport=None, **options):
        system = make_stage16_system(
            persistence_path=self.path,
            clock=fixtures.FixedClock(),
            persistence_time_factory=lambda: fixtures.NOW,
            intelligence_transport=transport or fixtures.FakeTransport(),
            **options,
        )
        self.systems.append(system)
        return system

    def send(self, system, text: str):
        self.sequence += 1
        return system.receive_text(fixtures.raw(f"legacy-{self.sequence}", text, self.sequence))

    def commit_legacy_records(self, system) -> None:
        # Recreate the pre-G17 stage16-v1 record layout, keeping the real
        # identity, owned state, output evidence and normal SQLite checksum.
        prefixes = ("dialogue-materials:", "conversation-memory:", "dialogue-turn:")
        system.core._records = [
            replace(record, entries=tuple(
                entry for entry in record.entries
                if not entry.source_reference.startswith(prefixes)
            ))
            for record in system.core.lifecycle_records
        ]
        system.snapshot_coordinator.commit(
            ExternalTime(fixtures.NOW.value + timedelta(seconds=self.sequence))
        )
        payload = system.persistence._connection.execute(
            "SELECT payload FROM checkpoints ORDER BY sequence DESC LIMIT 1"
        ).fetchone()["payload"]
        for prefix in prefixes:
            self.assertNotIn(prefix, payload)
        self.assertIn("stage16-v1", payload)

    def test_legacy_restore_preserves_owned_state_but_excludes_untraceable_history(self) -> None:
        first = self.make_system(fixtures.FakeTransport(reply="旧文脈にだけ存在する応答"))
        self.send(first, "覚えて：コーヒーはモカを選ぶ")
        self.send(first, "関係：配慮：産地も添える | 話題=コーヒー")
        relationship = self.send(first, "関係確認").relationship_change_record.after
        self.send(first, "コーヒーについて教えて")
        self.send(first, "経験：直前：長すぎた | 話題=コーヒー")
        experience = self.send(first, "経験確認").experience_change_record.after
        self.send(first, "コーヒーについてもう一度教えて")
        self.commit_legacy_records(first)
        subject_id = first.recovery.identity.subject_id
        memory_snapshot = first.memory.persistence_snapshot()
        relationship_snapshot = first.relationship.persistence_snapshot()
        old_turns = first.relationship.active_dialogue_context.turns
        old_records = first.core.lifecycle_records
        first.shutdown()

        restored = self.make_system(require_existing_persistence=True)
        self.assertEqual(restored.recovery.identity.subject_id, subject_id)
        self.assertEqual(restored.recovery.identity.configuration_version, "stage16-v1")
        self.assertEqual(restored.memory.persistence_snapshot(), memory_snapshot)
        self.assertEqual(restored.relationship.persistence_snapshot(), relationship_snapshot)
        self.assertEqual(restored.core.lifecycle_records, old_records)
        self.assertEqual(restored.relationship.active_dialogue_context.turns, old_turns)
        continued = self.send(restored, "コーヒーの続きを教えて")
        proposal = continued.intelligence_request.proposal
        self.assertEqual(proposal.dialogue_context.turns, ())
        self.assertEqual(proposal.conversation_memory.selections[0].meaning, "コーヒーはモカを選ぶ")
        self.assertEqual(proposal.relationship_information.selections[0].item, relationship)
        self.assertEqual(proposal.experience_information.selections[0].item, experience)

        following = self.send(restored, "コーヒーの産地も説明して").intelligence_request.proposal
        self.assertEqual(
            {turn.lifecycle_id for turn in following.dialogue_context.turns},
            {continued.context.lifecycle_id},
        )
        old_lifecycles = {turn.lifecycle_id for turn in old_turns}
        self.assertTrue(old_lifecycles.isdisjoint(
            turn.lifecycle_id for turn in following.dialogue_context.turns
        ))

    def test_legacy_physical_deletion_redacts_unknown_dependencies_and_checkpoint_payloads(self) -> None:
        transport = fixtures.FakeTransport(reply="合言葉に使う色は青い星です。")
        first = self.make_system(transport)
        self.send(first, "覚えて：合言葉は青い星")
        memory = first.memory.conversation_memory_items[0]
        self.send(first, "合言葉について教えて")
        transport.reply = "元の材料を特定できない古い説明です。"
        self.send(first, "もう一つ説明して")
        self.commit_legacy_records(first)
        subject_id = first.recovery.identity.subject_id
        old_lifecycles = {turn.lifecycle_id for turn in first.relationship.active_dialogue_context.turns}
        first.shutdown()

        restored = self.make_system(require_existing_persistence=True)
        before = "".join(row["payload"] for row in restored.persistence._connection.execute(
            "SELECT payload FROM checkpoints"
        ))
        self.assertIn("青い星", before)
        self.assertIn("元の材料を特定できない古い説明です。", before)
        pending = self.send(restored, "合言葉を完全に削除して")
        self.assertEqual(pending.conversation_memory_governance_record.status, ConversationMemoryOperationStatus.CONFIRMATION_REQUIRED)
        deleted = self.send(restored, "はい")
        self.assertEqual(deleted.conversation_memory_governance_record.status, ConversationMemoryOperationStatus.COMPLETED)
        self.assertEqual(deleted.conversation_memory_governance_record.residual_scopes, ())
        self.assertTrue(all(
            turn.text == REDACTED_DIALOGUE_TEXT
            for turn in restored.relationship.active_dialogue_context.turns
            if turn.lifecycle_id in old_lifecycles
        ))
        rows = restored.persistence._connection.execute("SELECT payload FROM checkpoints").fetchall()
        self.assertLessEqual(len(rows), 2)
        payloads = "".join(row["payload"] for row in rows)
        self.assertNotIn("青い星", payloads)
        self.assertNotIn("元の材料を特定できない古い説明です。", payloads)
        restored.shutdown()

        reopened = self.make_system(require_existing_persistence=True)
        self.assertEqual(reopened.recovery.identity.subject_id, subject_id)
        tombstone = reopened.memory.conversation_memory_items[0]
        self.assertEqual(tombstone.memory_item_id, memory.memory_item_id)
        self.assertTrue(tombstone.physically_deleted)
        self.assertTrue(old_lifecycles.issubset(
            record.lifecycle_id for record in reopened.core.lifecycle_records
        ))
        following = self.send(reopened, "合言葉について何を覚えてる？").intelligence_request.proposal
        self.assertEqual(following.conversation_memory.selections, ())
        self.assertTrue(all("青い星" not in turn.text for turn in following.dialogue_context.turns))


if __name__ == "__main__":
    unittest.main()
