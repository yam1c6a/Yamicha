"""G17: govern memory, relationship and experience in one running composition."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from yamicha.bootstrap import make_stage16_system  # noqa: E402
from yamicha.contracts import (  # noqa: E402
    ClockObservation,
    ConversationMemoryOperationKind,
    ConversationMemoryOperationStatus,
    ExternalIntelligenceResponse,
    ExternalTime,
    IntelligenceResultStatus,
    MonotonicTime,
    RawTextInput,
    SourceVerification,
)
from yamicha.contracts.experience import (  # noqa: E402
    ExperienceChangeStatus,
    ExperienceValue,
)
from yamicha.contracts.relationship import (  # noqa: E402
    RelationshipChangeStatus,
    RelationshipInformationStatus,
)


NOW = ExternalTime(datetime(2026, 10, 28, 0, 0, tzinfo=UTC))


class FixedClock:
    def observe(self) -> ClockObservation:
        return ClockObservation(external=NOW, monotonic=MonotonicTime(100.0))


class RecordingTransport:
    def __init__(self) -> None:
        self.requests = []
        self.reply = "現在の質問への説明です。"

    def generate(self, request):
        self.requests.append(request)
        return ExternalIntelligenceResponse(
            status=IntelligenceResultStatus.SUCCESS,
            model=request.proposal.model,
            content=json.dumps({"reply": self.reply}, ensure_ascii=False),
            detail="stage-17 governance integration transport",
        )


class Stage17GovernanceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "yamicha.sqlite3"
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

    def make_system(self, transport: RecordingTransport | None = None, **options):
        system = make_stage16_system(
            persistence_path=self.path,
            clock=FixedClock(),
            persistence_time_factory=lambda: NOW,
            intelligence_transport=transport or RecordingTransport(),
            **options,
        )
        self.systems.append(system)
        return system

    def send(self, system, text: str):
        self.sequence += 1
        return system.receive_text(RawTextInput(
            input_id=f"g17-governance-{self.sequence}",
            received_at=ExternalTime(NOW.value + timedelta(seconds=self.sequence)),
            source_id="human-001",
            content=text,
            source_verification=SourceVerification.VERIFIED,
        ))

    def seed_three_owners(self, system):
        self.send(system, "覚えて：合言葉は青い星")
        self.send(system, "関係：希望：説明には例を添える")
        relation = self.send(system, "関係確認").relationship_change_record.after
        self.send(system, "合言葉について説明して")
        pending = self.send(system, "経験：直前：長すぎた | 話題=合言葉")
        self.assertEqual(pending.experience_change_record.status, ExperienceChangeStatus.CONFIRMATION_REQUIRED)
        experience = self.send(system, "経験確認").experience_change_record.after
        memory = system.memory.conversation_memory_items[0]
        return memory, relation, experience

    def assert_other_owners_selected(self, outcome, relation, experience) -> None:
        proposal = outcome.intelligence_request.proposal
        self.assertEqual(tuple(s.item for s in proposal.relationship_information.selections), (relation,))
        self.assertEqual(tuple(s.item for s in proposal.experience_information.selections), (experience,))

    def assert_history_excludes(self, outcome, lifecycle_id: str, text: str) -> None:
        turns = outcome.intelligence_request.proposal.dialogue_context.turns
        self.assertNotIn(lifecycle_id, {turn.lifecycle_id for turn in turns})
        self.assertTrue(all(text not in turn.text for turn in turns))

    def test_memory_correction_excludes_prior_recall_with_relation_and_experience_unchanged(self) -> None:
        transport = RecordingTransport()
        system = self.make_system(transport)
        memory, relation, experience = self.seed_three_owners(system)
        transport.reply = "合言葉は青い星です。"
        used = self.send(system, "合言葉の内容を教えて")
        self.assertEqual(used.conversation_memory_window.selections[0].memory_item_id, memory.memory_item_id)

        corrected = self.send(system, "訂正：合言葉は青い星 → 合言葉は赤い月")
        record = corrected.conversation_memory_governance_record
        self.assertEqual(record.operation, ConversationMemoryOperationKind.CORRECTION)
        self.assertEqual(record.status, ConversationMemoryOperationStatus.COMPLETED)
        self.assertEqual(record.affected_items, ((memory.memory_item_id, 1, 2),))
        transport.reply = "更新された内容を説明します。"
        after = self.send(system, "合言葉の内容をもう一度教えて")

        self.assertEqual(tuple(s.meaning for s in after.conversation_memory_window.selections), ("合言葉は赤い月",))
        self.assert_other_owners_selected(after, relation, experience)
        self.assert_history_excludes(after, used.context.lifecycle_id, "青い星")

    def test_logical_forgetting_excludes_prior_recall_after_restart(self) -> None:
        transport = RecordingTransport()
        system = self.make_system(transport)
        memory, relation, experience = self.seed_three_owners(system)
        transport.reply = "合言葉は青い星です。"
        used = self.send(system, "合言葉の内容を教えて")
        forgotten = self.send(system, "合言葉を忘れて")
        self.assertEqual(forgotten.conversation_memory_governance_record.status, ConversationMemoryOperationStatus.COMPLETED)
        self.assertFalse(system.memory.conversation_memory_items[0].active)
        self.assertFalse(system.memory.conversation_memory_items[0].physically_deleted)
        system.shutdown()

        restored = self.make_system(require_existing_persistence=True)
        after = self.send(restored, "合言葉について何を覚えてる？")

        self.assertEqual(after.conversation_memory_window.selections, ())
        self.assert_other_owners_selected(after, relation, experience)
        self.assertEqual(restored.memory.conversation_memory_items[0].memory_item_id, memory.memory_item_id)
        self.assert_history_excludes(after, used.context.lifecycle_id, "青い星")

    def test_deactivation_excludes_prior_recall_without_changing_other_owners(self) -> None:
        transport = RecordingTransport()
        system = self.make_system(transport)
        _, relation, experience = self.seed_three_owners(system)
        transport.reply = "合言葉は青い星です。"
        used = self.send(system, "合言葉の内容を教えて")
        deactivated = self.send(system, "合言葉を無効にして")
        self.assertEqual(deactivated.conversation_memory_governance_record.operation, ConversationMemoryOperationKind.DEACTIVATION)
        self.assertEqual(deactivated.conversation_memory_governance_record.status, ConversationMemoryOperationStatus.COMPLETED)
        transport.reply = "現在の情報だけで説明します。"
        after = self.send(system, "合言葉について説明して")

        self.assertEqual(after.conversation_memory_window.selections, ())
        self.assert_other_owners_selected(after, relation, experience)
        self.assert_history_excludes(after, used.context.lifecycle_id, "青い星")

    def test_physical_deletion_removes_recalled_content_and_preserves_other_owners(self) -> None:
        transport = RecordingTransport()
        system = self.make_system(transport)
        _, relation, experience = self.seed_three_owners(system)
        transport.reply = "合言葉は青い星"
        used = self.send(system, "合言葉の内容を教えて")
        requested = self.send(system, "合言葉を完全に削除して")
        self.assertEqual(requested.conversation_memory_governance_record.status, ConversationMemoryOperationStatus.CONFIRMATION_REQUIRED)
        system.shutdown()
        restored = self.make_system(require_existing_persistence=True)
        deleted = self.send(restored, "はい")
        self.assertEqual(deleted.conversation_memory_governance_record.status, ConversationMemoryOperationStatus.COMPLETED)
        self.assertEqual(deleted.conversation_memory_governance_record.residual_scopes, ())
        self.assertTrue(restored.memory.conversation_memory_items[0].physically_deleted)
        after = self.send(restored, "合言葉について何を覚えてる？")

        self.assertEqual(after.conversation_memory_window.selections, ())
        self.assert_other_owners_selected(after, relation, experience)
        self.assert_history_excludes(after, used.context.lifecycle_id, "青い星")
        rows = restored.persistence._connection.execute("SELECT payload FROM checkpoints").fetchall()
        self.assertLessEqual(len(rows), 2)
        self.assertNotIn("合言葉は青い星", "".join(row["payload"] for row in rows))
        restored.shutdown()
        reopened = self.make_system(require_existing_persistence=True)
        self.assertTrue(reopened.memory.conversation_memory_items[0].physically_deleted)
        self.assertEqual(reopened.relationship.relationship_items, (relation,))
        self.assertEqual(reopened.memory.experience_items, (experience,))

    def test_three_pending_confirmations_are_independent_across_restart(self) -> None:
        transport = RecordingTransport()
        system = self.make_system(transport)
        self.send(system, "覚えて：削除対象は緑の石")
        self.send(system, "通常の説明をお願い？")
        self.send(system, "経験：直前：長すぎた")
        self.send(system, "関係：希望：例を添える")
        self.send(system, "緑の石を完全に削除して")
        self.assertEqual(len(system.memory.experience_pending_confirmations), 1)
        self.assertEqual(len(system.relationship.relationship_pending_confirmations), 1)
        self.assertEqual(len(system.memory.conversation_memory_pending_confirmations), 1)
        self.assertEqual(len(transport.requests), 1)
        system.shutdown()
        restored_transport = RecordingTransport()
        restored = self.make_system(restored_transport, require_existing_persistence=True)

        relation = self.send(restored, "関係確認")
        self.assertEqual(relation.relationship_change_record.status, RelationshipChangeStatus.COMPLETED)
        self.assertEqual(len(restored.memory.experience_pending_confirmations), 1)
        self.assertEqual(len(restored.memory.conversation_memory_pending_confirmations), 1)
        self.assertTrue(restored.memory.conversation_memory_items[0].active)
        experience = self.send(restored, "経験確認")
        self.assertEqual(experience.experience_change_record.status, ExperienceChangeStatus.COMPLETED)
        self.assertEqual(len(restored.memory.conversation_memory_pending_confirmations), 1)
        self.assertTrue(restored.memory.conversation_memory_items[0].active)
        deleted = self.send(restored, "はい")
        self.assertEqual(deleted.conversation_memory_governance_record.status, ConversationMemoryOperationStatus.COMPLETED)
        self.assertEqual(restored_transport.requests, [])

        after = self.send(restored, "次の説明をお願い")
        proposal = after.intelligence_request.proposal
        self.assertEqual(proposal.constraints.max_output_characters, 160)
        self.assertEqual(len(proposal.relationship_information.selections), 1)
        self.assertEqual(proposal.conversation_memory.selections, ())
        self.assertTrue(all(not turn.text.startswith(("経験", "関係", "覚えて")) for turn in proposal.dialogue_context.turns))
        self.assertEqual(restored.memory.experience_pending_confirmations, ())
        self.assertEqual(restored.relationship.relationship_pending_confirmations, ())
        self.assertEqual(restored.memory.conversation_memory_pending_confirmations, ())

    def test_relation_correction_withdrawal_and_experience_reassessment_keep_only_current_versions(self) -> None:
        transport = RecordingTransport()
        system = self.make_system(transport)
        memory, relation, experience = self.seed_three_owners(system)
        transport.reply = "旧関係と旧経験を使った説明です。"
        first_usage = self.send(system, "合言葉について説明して")
        self.send(system, f"関係訂正：{relation.item_id}：例より手順を優先する")
        corrected = self.send(system, "関係確認").relationship_change_record.after
        self.assertEqual(corrected.version, 2)
        self.send(system, f"経験見直し：{experience.item_id}：説明が足りなかった")
        revised = self.send(system, "経験確認").experience_change_record.after
        self.assertEqual(revised.value, ExperienceValue.DETAILED)
        self.assertEqual(revised.version, 2)
        transport.reply = "新しい関係と経験に従う説明です。"
        current = self.send(system, "合言葉についてもう一度説明して")
        self.assert_other_owners_selected(current, corrected, revised)
        self.assert_history_excludes(current, first_usage.context.lifecycle_id, "旧関係と旧経験")
        self.assertGreater(current.intelligence_request.proposal.constraints.max_output_characters, 160)
        self.assertEqual(current.conversation_memory_window.selections[0].memory_item_id, memory.memory_item_id)

        withdrawn = self.send(system, f"関係撤回：{relation.item_id}")
        self.assertEqual(withdrawn.relationship_change_record.after.status, RelationshipInformationStatus.WITHDRAWN)
        pending = self.send(system, f"経験不採用：{experience.item_id}")
        self.assertEqual(pending.experience_change_record.status, ExperienceChangeStatus.CONFIRMATION_REQUIRED)
        self.assertTrue(system.memory.experience_items[0].active)
        rejected = self.send(system, "経験確認").experience_change_record.after
        self.assertFalse(rejected.active)
        self.assertEqual(rejected.version, 3)
        system.shutdown()
        restored = self.make_system(require_existing_persistence=True)
        after = self.send(restored, "合言葉について説明して")
        self.assertEqual(after.relationship_information.selections, ())
        self.assertEqual(after.experience_information.selections, ())
        self.assertEqual(after.conversation_memory_window.selections[0].memory_item_id, memory.memory_item_id)
        self.assert_history_excludes(after, current.context.lifecycle_id, "新しい関係と経験")
        self.assertEqual(restored.memory.experience_changes[-1].before, revised)
        self.assertEqual(restored.memory.experience_changes[-1].after, rejected)

    def test_expired_relation_is_removed_from_history_while_scoped_memory_and_experience_continue(self) -> None:
        transport = RecordingTransport()
        system = self.make_system(transport)
        self.send(system, "覚えて：合言葉は青い星")
        self.send(system, "関係：配慮：期間限定の例を添える | 話題=合言葉 | 期限=2026-10-28T00:01:00+00:00")
        relation = self.send(system, "関係確認").relationship_change_record.after
        self.send(system, "合言葉について説明して")
        self.send(system, "経験：直前：長すぎた | 話題=合言葉")
        experience = self.send(system, "経験確認").experience_change_record.after
        transport.reply = "期間限定の例を添えた説明です。"
        used = self.send(system, "合言葉の内容を教えて")
        self.assert_other_owners_selected(used, relation, experience)
        self.sequence = 60
        transport.reply = "期限後の説明です。"
        after = self.send(system, "合言葉の内容を教えて")

        self.assertEqual(after.relationship_information.selections, ())
        self.assertEqual(tuple(s.item for s in after.experience_information.selections), (experience,))
        self.assertEqual(len(after.conversation_memory_window.selections), 1)
        self.assert_history_excludes(after, used.context.lifecycle_id, "期間限定")

    def test_memory_omitted_from_selection_cannot_reappear_through_dialogue_history(self) -> None:
        transport = RecordingTransport()
        system = self.make_system(transport)
        self.send(system, "覚えて：合言葉は青い星")
        transport.reply = "合言葉は青い星です。"
        direct = self.send(system, "合言葉の内容を教えて")
        transport.reply = "現在の入力に沿って続きを説明します。"
        indirect = self.send(system, "続きを説明して")
        self.assertEqual(indirect.conversation_memory_window.selections, ())
        self.assert_history_excludes(indirect, direct.context.lifecycle_id, "青い星")
        self.send(system, "合言葉を忘れて")
        system.shutdown()

        restored = self.make_system(require_existing_persistence=True)
        after = self.send(restored, "続けて説明して")
        self.assert_history_excludes(after, direct.context.lifecycle_id, "青い星")
        self.assertIn(indirect.context.lifecycle_id, {
            turn.lifecycle_id for turn in after.intelligence_request.proposal.dialogue_context.turns
        })

    def test_forgetting_excludes_original_preference_input_from_dialogue_history(self) -> None:
        system = self.make_system()
        original = self.send(system, "私はコーヒーが好き")
        self.assertEqual(len(system.memory.conversation_memory_items), 1)
        forgotten = self.send(system, "コーヒーを忘れて")
        self.assertEqual(forgotten.conversation_memory_governance_record.status, ConversationMemoryOperationStatus.COMPLETED)

        after = self.send(system, "私の好みについて教えて")

        self.assertEqual(after.conversation_memory_window.selections, ())
        self.assert_history_excludes(after, original.context.lifecycle_id, "コーヒーが好き")

    def test_physical_deletion_redacts_paraphrased_multiturn_recall_and_its_checkpoints(self) -> None:
        transport = RecordingTransport()
        system = self.make_system(transport)
        self.send(system, "覚えて：合言葉は青い星")
        transport.reply = "合言葉は青い星です。"
        direct = self.send(system, "合言葉の内容を教えて")
        transport.reply = "前に挙げた星の色を、夜空の目印にしてください。"
        derived = self.send(system, "合言葉の使い方も説明して")
        self.assertIn(direct.context.lifecycle_id, {
            turn.lifecycle_id for turn in derived.intelligence_request.proposal.dialogue_context.turns
        })
        self.send(system, "合言葉を完全に削除して")
        system.shutdown()

        restored = self.make_system(require_existing_persistence=True)
        deleted = self.send(restored, "はい")
        self.assertEqual(deleted.conversation_memory_governance_record.status, ConversationMemoryOperationStatus.COMPLETED)
        after = self.send(restored, "続けて説明して")
        self.assert_history_excludes(after, direct.context.lifecycle_id, "青い星")
        self.assert_history_excludes(after, derived.context.lifecycle_id, "夜空の目印")
        payloads = "".join(row["payload"] for row in restored.persistence._connection.execute(
            "SELECT payload FROM checkpoints"
        ).fetchall())
        self.assertNotIn("青い星", payloads)
        self.assertNotIn("夜空の目印", payloads)


if __name__ == "__main__":
    unittest.main()
