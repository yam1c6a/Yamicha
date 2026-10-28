"""G17: deleting governed content must include budget-excluded source turns."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import test_stage16_experience_dialogue as fixtures
from yamicha.bootstrap import make_stage16_system
from yamicha.contracts import ConversationMemoryOperationStatus
from yamicha.life.stage14.relationship import REDACTED_DIALOGUE_TEXT


class Stage17DeletionRegressionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "governed-deletion.sqlite3"
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

    def make_system(self, **options):
        system = make_stage16_system(
            persistence_path=self.path,
            clock=fixtures.FixedClock(),
            persistence_time_factory=lambda: fixtures.NOW,
            intelligence_transport=fixtures.FakeTransport(),
            **options,
        )
        self.systems.append(system)
        return system

    def send(self, system, text: str):
        self.sequence += 1
        return system.receive_text(fixtures.raw(f"deletion-regression-{self.sequence}", text, self.sequence))

    def assert_deleted(self, system, *, lifecycles: set[str], contents: tuple[str, ...]) -> None:
        relevant_turns = tuple(
            turn for turn in system.relationship.active_dialogue_context.turns
            if turn.lifecycle_id in lifecycles
        )
        self.assertEqual({turn.lifecycle_id for turn in relevant_turns}, lifecycles)
        self.assertTrue(all(turn.text == REDACTED_DIALOGUE_TEXT for turn in relevant_turns))
        rows = system.persistence._connection.execute("SELECT payload FROM checkpoints").fetchall()
        self.assertTrue(rows)
        self.assertLessEqual(len(rows), system.retention_policy.checkpoint_generations)
        for row in rows:
            for content in contents:
                self.assertNotIn(content, row["payload"])
        self.assertTrue(lifecycles.issubset(
            record.lifecycle_id for record in system.core.lifecycle_records
        ))

    def test_long_memory_correction_input_is_deleted_without_selection_trace(self) -> None:
        system = self.make_system()
        original = self.send(system, "覚えて：長い記憶" + "あ" * 1100)
        corrected = self.send(system, "訂正：長い記憶 → 合言葉は赤い月")
        self.assertEqual(corrected.conversation_memory_window.selections, ())
        self.assertEqual(corrected.conversation_memory_governance_record.status, ConversationMemoryOperationStatus.COMPLETED)
        self.assertFalse(any(entry.source_reference.startswith("conversation-memory:")
                             for entry in corrected.lifecycle_record.entries))
        self.assertTrue(any(entry.source_reference.startswith("memory-governance-target:")
                            for entry in corrected.lifecycle_record.entries))
        self.assertEqual(corrected.lifecycle_record, next(
            record for record in system.core.lifecycle_records
            if record.lifecycle_id == corrected.context.lifecycle_id
        ))

        pending = self.send(system, "赤い月を完全に削除して")
        self.assertEqual(pending.conversation_memory_governance_record.status, ConversationMemoryOperationStatus.CONFIRMATION_REQUIRED)
        self.assertTrue(any("赤い月" in turn.text for turn in system.relationship.active_dialogue_context.turns))
        deleted = self.send(system, "はい")

        self.assertEqual(deleted.conversation_memory_governance_record.status, ConversationMemoryOperationStatus.COMPLETED)
        self.assertEqual(deleted.conversation_memory_governance_record.residual_scopes, ())
        self.assert_deleted(system,
            lifecycles={original.context.lifecycle_id, corrected.context.lifecycle_id},
            contents=("長い記憶", "合言葉は赤い月"),
        )

    def test_multiple_budget_excluded_corrections_are_deleted_across_restarts(self) -> None:
        system = self.make_system()
        original = self.send(system, "覚えて：秘密の記憶" + "い" * 1100)
        first = self.send(system, "訂正：秘密の記憶 → 合言葉は銀の森")
        self.send(system, "覚えて：夕食はカレー")
        unrelated = next(item for item in system.memory.conversation_memory_items if item.meaning == "夕食はカレー")
        system.shutdown()

        restored = self.make_system(require_existing_persistence=True)
        second = self.send(restored, "訂正：銀の森 → 金の湖")
        self.assertEqual(second.conversation_memory_window.selections, ())
        self.assertEqual(second.conversation_memory_governance_record.status, ConversationMemoryOperationStatus.COMPLETED)
        self.send(restored, "金の湖を完全に削除して")
        restored.shutdown()

        confirmed = self.make_system(require_existing_persistence=True)
        deleted = self.send(confirmed, "はい")
        self.assertEqual(deleted.conversation_memory_governance_record.status, ConversationMemoryOperationStatus.COMPLETED)
        self.assertEqual(next(item for item in confirmed.memory.conversation_memory_items
                              if item.memory_item_id == unrelated.memory_item_id), unrelated)
        ids = {original.context.lifecycle_id, first.context.lifecycle_id, second.context.lifecycle_id}
        self.assert_deleted(confirmed, lifecycles=ids, contents=("秘密の記憶", "銀の森", "金の湖"))
        confirmed.shutdown()

        reopened = self.make_system(require_existing_persistence=True)
        self.assert_deleted(reopened, lifecycles=ids, contents=("秘密の記憶", "銀の森", "金の湖"))
        self.assertEqual(next(item for item in reopened.memory.conversation_memory_items
                              if item.memory_item_id == unrelated.memory_item_id), unrelated)

    def test_current_input_budget_exclusion_and_cancel_preserve_content_until_confirmed(self) -> None:
        system = self.make_system(intelligence_max_input_characters=32)
        self.send(system, "覚えて：合言葉は青い星")
        correction = self.send(system, "訂正：青い星 → 秘密の白い岬" + "う" * 40)
        self.assertEqual(correction.conversation_memory_window.selections, ())
        self.assertEqual(correction.conversation_memory_governance_record.status, ConversationMemoryOperationStatus.COMPLETED)
        first_request = self.send(system, "白い岬を完全に削除して")
        cancelled = self.send(system, "いいえ")
        self.assertEqual(cancelled.conversation_memory_governance_record.status, ConversationMemoryOperationStatus.CANCELLED)
        self.assertTrue(any("白い岬" in turn.text for turn in system.relationship.active_dialogue_context.turns))
        system.shutdown()
        restored = self.make_system(require_existing_persistence=True, intelligence_max_input_characters=32)
        self.send(restored, "白い岬を完全に削除して")
        deleted = self.send(restored, "はい")
        self.assertEqual(deleted.conversation_memory_governance_record.status, ConversationMemoryOperationStatus.COMPLETED)
        self.assert_deleted(restored,
            lifecycles={correction.context.lifecycle_id, first_request.context.lifecycle_id},
            contents=("青い星", "白い岬"),
        )


if __name__ == "__main__":
    unittest.main()
