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
from yamicha.contracts import ExternalTime
from yamicha.contracts.experience import ExperienceChangeStatus, ExperienceValue


class Stage16TopicReassessmentTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "topic.sqlite3"
        self.systems = []
        self.sequence = 0

    def tearDown(self):
        for system in reversed(self.systems):
            if not system._closed:
                if system.text_input_runner.persistence_healthy:
                    system.shutdown()
                else:
                    system.abandon()
        self.directory.cleanup()

    def make_system(self):
        system = make_stage16_system(
            persistence_path=self.path,
            clock=fixtures.FixedClock(), persistence_time_factory=lambda: fixtures.NOW,
            intelligence_transport=fixtures.FakeTransport(),
        )
        self.systems.append(system)
        return system

    def send(self, system, text):
        self.sequence += 1
        return system.receive_text(fixtures.raw(f"topic-{self.sequence}", text, self.sequence))

    def new_context(self, system):
        self.sequence += 1
        return system.start_new_dialogue_context(
            ExternalTime(fixtures.NOW.value + timedelta(seconds=self.sequence))
        )

    def adopt(self, system, *, topic=True):
        self.send(system, "コーヒーについて教えて")
        suffix = " | 話題=コーヒー" if topic else ""
        self.send(system, "経験：直前：長すぎた" + suffix)
        return self.send(system, "経験確認").experience_change_record.after

    def test_new_context_reassessment_preserves_identity_scope_and_restart(self):
        system = self.make_system()
        original = self.adopt(system)
        active = self.new_context(system)
        used = self.send(system, "コーヒーの淹れ方について教えて")
        self.assertEqual(used.experience_information.selections[0].item, original)

        pending = self.send(system, f"経験見直し：{original.item_id}：説明が足りなかった")

        self.assertEqual(pending.experience_change_record.status, ExperienceChangeStatus.CONFIRMATION_REQUIRED)
        self.assertEqual(pending.experience_pending_confirmation.request.context_id, active.context_id)
        self.assertEqual(system.memory.experience_items, (original,))
        pending_snapshot = system.memory.persistence_snapshot()
        system.shutdown()
        restored = self.make_system()
        self.assertEqual(restored.memory.persistence_snapshot(), pending_snapshot)

        change = self.send(restored, "経験確認").experience_change_record

        revised = change.after
        self.assertEqual(change.before, original)
        self.assertEqual(revised.item_id, original.item_id)
        self.assertEqual(revised.version, 2)
        self.assertEqual(revised.context_id, original.context_id)
        self.assertEqual(revised.scope_keywords, original.scope_keywords)
        self.assertEqual(revised.value, ExperienceValue.DETAILED)
        self.assertNotEqual(revised.evidence_id, original.evidence_id)
        adopted_snapshot = restored.memory.persistence_snapshot()
        restored.shutdown()
        resumed = self.make_system()
        self.assertEqual(resumed.memory.persistence_snapshot(), adopted_snapshot)
        following = self.send(resumed, "コーヒーの違いを教えて")
        self.assertEqual(following.experience_information.selections[0].item, revised)
        self.assertEqual(following.intelligence_request.proposal.constraints.max_output_characters, 800)
        self.assertNotIn(used.context.lifecycle_id, {
            turn.lifecycle_id for turn in following.intelligence_request.proposal.dialogue_context.turns
        })

    def test_new_context_reassessment_rejects_unrelated_latest_output(self):
        system = self.make_system()
        original = self.adopt(system)
        self.new_context(system)
        self.send(system, "コーヒーを説明して")
        unrelated = self.send(system, "天気について教えて")
        self.assertEqual(unrelated.experience_information.selections, ())

        rejected = self.send(system, f"経験見直し：{original.item_id}：説明が足りなかった")

        self.assertEqual(rejected.experience_change_record.status, ExperienceChangeStatus.REJECTED)
        self.assertEqual(system.memory.experience_pending_confirmations, ())
        self.assertEqual(system.memory.experience_items, (original,))

    def test_context_only_experience_cannot_be_reassessed_outside_its_scope(self):
        system = self.make_system()
        original = self.adopt(system, topic=False)
        self.new_context(system)
        self.send(system, "コーヒーを説明して")

        rejected = self.send(system, f"経験見直し：{original.item_id}：説明が足りなかった")

        self.assertEqual(rejected.experience_change_record.status, ExperienceChangeStatus.REJECTED)
        self.assertEqual(system.memory.experience_items, (original,))

    def test_new_context_reassessment_cannot_expand_the_confirmed_topic_scope(self):
        system = self.make_system()
        original = self.adopt(system)
        self.new_context(system)
        self.send(system, "コーヒーを説明して")

        rejected = self.send(system, f"経験見直し：{original.item_id}：説明が足りなかった | 話題=コーヒー,紅茶")

        self.assertEqual(rejected.experience_change_record.status, ExperienceChangeStatus.REJECTED)
        self.assertEqual(system.memory.experience_items, (original,))

    def test_cross_context_proof_is_required_for_pending_and_adopted_snapshots(self):
        system = self.make_system()
        original = self.adopt(system)
        self.new_context(system)
        self.send(system, "コーヒーを説明して")
        self.send(system, f"経験見直し：{original.item_id}：説明が足りなかった")
        pending_snapshot = system.memory.persistence_snapshot()
        with self.assertRaises(ValueError):
            replace(pending_snapshot, experience_usages=())
        self.send(system, "経験確認")
        adopted_snapshot = system.memory.persistence_snapshot()
        with self.assertRaises(ValueError):
            replace(adopted_snapshot, experience_usages=())
        wrong_usage = replace(adopted_snapshot.experience_usages[0],
                              item_versions=((original.item_id, 2),))
        with self.assertRaises(ValueError):
            replace(adopted_snapshot, experience_usages=(wrong_usage,))

    def test_reassessment_can_continue_across_contexts_then_be_rejected(self):
        system = self.make_system()
        original = self.adopt(system)
        self.new_context(system)
        self.send(system, "コーヒーを説明して")
        self.send(system, f"経験見直し：{original.item_id}：説明が足りなかった")
        second = self.send(system, "経験確認").experience_change_record.after
        self.new_context(system)
        applied = self.send(system, "コーヒーの続きについて教えて")
        self.assertEqual(applied.experience_information.selections[0].item, second)
        self.send(system, f"経験見直し：{original.item_id}：長すぎた")
        third = self.send(system, "経験確認").experience_change_record.after
        self.assertEqual(third.version, 3)
        self.assertEqual(third.value, ExperienceValue.BRIEF)
        self.assertEqual(third.context_id, original.context_id)
        self.send(system, f"経験不採用：{original.item_id}")
        inactive = self.send(system, "経験確認").experience_change_record.after
        self.assertFalse(inactive.active)
        self.assertEqual(inactive.version, 4)
        snapshot = system.memory.persistence_snapshot()
        system.shutdown()
        resumed = self.make_system()
        self.assertEqual(resumed.memory.persistence_snapshot(), snapshot)


if __name__ == "__main__":
    unittest.main()
