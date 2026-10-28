"""G17: bounded dialogue and retained material share one stage-16 composition."""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import test_stage16_experience_dialogue as fixtures
from yamicha.bootstrap import make_stage16_system
from yamicha.contracts import ExternalTime, InputDisposition, PreviousExit
from yamicha.contracts.experience import ExperienceChangeStatus
from yamicha.contracts.relationship import RelationshipChangeStatus


class Stage17ContinuityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "continuity.sqlite3"
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
        return system.receive_text(fixtures.raw(f"continuity-{self.sequence}", text, self.sequence))

    def new_context(self, system):
        self.sequence += 1
        return system.start_new_dialogue_context(
            ExternalTime(fixtures.NOW.value + timedelta(seconds=self.sequence))
        )

    def add_relationship(self, system, text: str):
        pending = self.send(system, text)
        self.assertEqual(pending.relationship_change_record.status, RelationshipChangeStatus.CONFIRMATION_REQUIRED)
        changed = self.send(system, "関係確認").relationship_change_record
        self.assertEqual(changed.status, RelationshipChangeStatus.COMPLETED)
        return changed.after

    def add_experience(self, system, reaction: str = "長すぎた", *, topic: str = ""):
        self.send(system, "コーヒーの説明をお願い")
        suffix = f" | 話題={topic}" if topic else ""
        pending = self.send(system, f"経験：直前：{reaction}{suffix}")
        self.assertEqual(pending.experience_change_record.status, ExperienceChangeStatus.CONFIRMATION_REQUIRED)
        changed = self.send(system, "経験確認").experience_change_record
        self.assertEqual(changed.status, ExperienceChangeStatus.COMPLETED)
        return changed.after

    def assert_budget(self, proposal) -> None:
        dialogue = proposal.dialogue_context
        memory = proposal.conversation_memory
        relationship = proposal.relationship_information
        experience = proposal.experience_information
        self.assertEqual(dialogue.current_input_characters, len(proposal.input_text))
        self.assertLessEqual(len({turn.lifecycle_id for turn in dialogue.turns}), 6)
        self.assertLessEqual(len(memory.selections), 5)
        self.assertLessEqual(memory.total_characters, 1024)
        self.assertLessEqual(len(relationship.selections), 5)
        self.assertLessEqual(relationship.total_characters, 512)
        self.assertLessEqual(len(experience.selections), 2)
        self.assertLessEqual(experience.total_characters, 256)
        self.assertLessEqual(
            dialogue.total_characters + memory.total_characters
            + relationship.total_characters + experience.total_characters,
            4096,
        )

    def test_ycc_t01_long_dialogue_shares_limits_and_preserves_current_input(self) -> None:
        transport = fixtures.FakeTransport(reply="確認した説明です。" * 10)
        system = self.make_system(transport)
        for index in range(7):
            self.send(system, f"覚えて：コーヒーの資料{index}は" + chr(0x3042 + index) * 180)
            self.add_relationship(system, f"関係：希望：希望{index}" + "あ" * 100)
        self.add_experience(system)
        self.add_experience(system, "確認せず答えてほしかった")
        self.assertEqual(len(system.memory.conversation_memory_items), 7)
        self.assertEqual(len(system.relationship.relationship_items), 7)
        self.assertEqual(len(system.memory.experience_items), 2)

        ordinary = []
        for index in range(18):
            outcome = self.send(system, f"コーヒーの話題{index}を説明して。" + "豆" * 200)
            proposal = outcome.intelligence_request.proposal
            self.assert_budget(proposal)
            self.assertTrue(proposal.conversation_memory.selections)
            self.assertTrue(proposal.relationship_information.selections)
            self.assertEqual(len(proposal.experience_information.selections), 2)
            self.assertLessEqual(len(outcome.dialogue_output.text), 160)
            selected_cycles = {turn.lifecycle_id for turn in proposal.dialogue_context.turns}
            if index >= 6:
                self.assertTrue(selected_cycles.issubset(set(ordinary[-6:])))
            ordinary.append(outcome.context.lifecycle_id)

        long_input = "コーヒー" + "あ" * (4096 - len("コーヒー"))
        proposal = self.send(system, long_input).intelligence_request.proposal
        self.assertEqual(proposal.input_text, long_input)
        self.assert_budget(proposal)
        self.assertEqual(proposal.dialogue_context.turns, ())
        self.assertEqual(proposal.conversation_memory.selections, ())
        self.assertEqual(proposal.relationship_information.selections, ())
        self.assertEqual(proposal.experience_information.selections, ())

    def test_ycc_t02_mixed_state_restores_and_new_context_uses_only_relevant_material(self) -> None:
        first = self.make_system()
        retained = self.send(first, "覚えて：コーヒーはモカを選ぶ")
        memory = first.memory.conversation_memory_items[0]
        relationship = self.add_relationship(first, "関係：配慮：コーヒーの説明では産地も添える | 話題=コーヒー")
        experience = self.add_experience(first, topic="コーヒー")
        used = self.send(first, "コーヒーの選び方を教えて")
        subject_id = first.recovery.identity.subject_id
        memory_snapshot = first.memory.persistence_snapshot()
        relationship_snapshot = first.relationship.persistence_snapshot()
        lifecycle_records = first.core.lifecycle_records
        first.shutdown()

        replacement = fixtures.FakeTransport(reply="別の補助知能による説明")
        restored = self.make_system(replacement, require_existing_persistence=True, intelligence_model="replacement-local-model")
        self.assertEqual(restored.recovery.previous_exit, PreviousExit.NORMAL)
        self.assertEqual(restored.recovery.identity.subject_id, subject_id)
        self.assertEqual(restored.memory.persistence_snapshot(), memory_snapshot)
        self.assertEqual(restored.relationship.persistence_snapshot(), relationship_snapshot)
        self.assertEqual(restored.core.lifecycle_records, lifecycle_records)
        continued = self.send(restored, "コーヒーの説明を続けて")
        proposal = continued.intelligence_request.proposal
        self.assertEqual(proposal.model, "replacement-local-model")
        self.assertIn(used.context.lifecycle_id, {turn.lifecycle_id for turn in proposal.dialogue_context.turns})
        self.assertEqual(proposal.conversation_memory.selections[0].memory_item_id, memory.memory_item_id)
        self.assertEqual(proposal.relationship_information.selections[0].item, relationship)
        self.assertEqual(proposal.experience_information.selections[0].item, experience)
        self.assertEqual(memory.source_references, retained.conversation_memory_candidate.source_references)
        self.assert_budget(proposal)

        old_context = restored.relationship.active_dialogue_context.context_id
        active = self.new_context(restored)
        self.assertNotEqual(active.context_id, old_context)
        self.assertEqual(active.previous_context_id, old_context)
        unrelated = self.send(restored, "数学の集合について説明して").intelligence_request.proposal
        self.assertEqual(unrelated.dialogue_context.turns, ())
        self.assertEqual(unrelated.conversation_memory.selections, ())
        self.assertEqual(unrelated.relationship_information.selections, ())
        self.assertEqual(unrelated.experience_information.selections, ())
        relevant = self.send(restored, "コーヒーについて教えて").intelligence_request.proposal
        self.assertEqual(relevant.conversation_memory.selections[0].memory_item_id, memory.memory_item_id)
        self.assertEqual(relevant.relationship_information.selections[0].item, relationship)
        self.assertEqual(relevant.experience_information.selections[0].item, experience)
        self.assertTrue(all(turn.context_id == active.context_id for turn in relevant.dialogue_context.turns))

    def test_ycc_t03_revised_material_keeps_provenance_versions_and_usage_after_restart(self) -> None:
        system = self.make_system()
        self.send(system, "覚えて：コーヒーはモカを選ぶ")
        old_memory = system.memory.conversation_memory_items[0]
        old_relationship = self.add_relationship(system, "関係：希望：例を一つ添える | 話題=コーヒー")
        old_experience = self.add_experience(system, topic="コーヒー")
        old_used = self.send(system, "コーヒーについて教えて")
        self.send(system, "訂正：コーヒーはモカを選ぶ → コーヒーはキリマンジャロを選ぶ")
        self.send(system, f"関係訂正：{old_relationship.item_id}：例を二つ添える")
        relation_change = self.send(system, "関係確認").relationship_change_record
        self.send(system, "コーヒーの説明をお願い")
        self.send(system, f"経験見直し：{old_experience.item_id}：説明が足りなかった")
        experience_change = self.send(system, "経験確認").experience_change_record
        memory = next(item for item in system.memory.conversation_memory_items if item.active)
        relationship = relation_change.after
        experience = experience_change.after
        self.assertEqual(memory.version, old_memory.version + 1)
        self.assertEqual(relationship.version, old_relationship.version + 1)
        self.assertEqual(experience.version, old_experience.version + 1)
        self.assertTrue(memory.source_references)
        self.assertTrue(relation_change.reason)
        self.assertEqual(relation_change.before, old_relationship)
        self.assertTrue(experience_change.reason)
        self.assertEqual(experience_change.before, old_experience)

        used = self.send(system, "コーヒーについて教えて")
        proposal = used.intelligence_request.proposal
        selection = proposal.conversation_memory.selections[0]
        self.assertEqual((selection.memory_item_id, selection.memory_item_version), (memory.memory_item_id, memory.version))
        self.assertEqual(selection.certainty, memory.certainty)
        self.assertEqual(selection.source_references, memory.source_references)
        self.assertTrue(selection.selection_reason)
        self.assertEqual(proposal.relationship_information.selections[0].item, relationship)
        self.assertEqual(proposal.experience_information.selections[0].item, experience)
        self.assertNotIn(old_used.context.lifecycle_id, {turn.lifecycle_id for turn in proposal.dialogue_context.turns})
        self.assertEqual(system.relationship.relationship_usages[-1].item_versions, ((relationship.item_id, relationship.version),))
        self.assertEqual(system.memory.experience_usages[-1].item_versions, ((experience.item_id, experience.version),))
        expected_memory = system.memory.persistence_snapshot()
        expected_relationship = system.relationship.persistence_snapshot()
        system.shutdown()

        restored = self.make_system(require_existing_persistence=True)
        self.assertEqual(restored.memory.persistence_snapshot(), expected_memory)
        self.assertEqual(restored.relationship.persistence_snapshot(), expected_relationship)
        record = next(record for record in restored.core.lifecycle_records if record.lifecycle_id == used.context.lifecycle_id)
        references = {entry.source_reference for entry in record.entries}
        self.assertIn(f"experience:{experience.item_id}:v{experience.version}", references)
        self.assertIn(f"conversation-memory:{memory.memory_item_id}:v{memory.version}", references)

    def test_ycc_t04_pending_owners_restore_and_duplicate_confirmation_does_not_reapply(self) -> None:
        system = self.make_system()
        self.send(system, "覚えて：コーヒーはモカを選ぶ")
        self.send(system, "コーヒーについて教えて")
        self.send(system, "関係：希望：産地も添える | 話題=コーヒー")
        self.send(system, "経験：直前：長すぎた | 話題=コーヒー")
        self.assertEqual(system.relationship.relationship_items, ())
        self.assertEqual(system.memory.experience_items, ())
        pending_relationship = system.relationship.relationship_pending_confirmations
        pending_experience = system.memory.experience_pending_confirmations
        self.assertEqual(len(pending_relationship), 1)
        self.assertEqual(len(pending_experience), 1)
        system.shutdown()

        restored = self.make_system(require_existing_persistence=True)
        self.assertEqual(restored.relationship.relationship_pending_confirmations, pending_relationship)
        self.assertEqual(restored.memory.experience_pending_confirmations, pending_experience)
        before = self.send(restored, "コーヒーについて教えて").intelligence_request.proposal
        self.assertEqual(before.relationship_information.selections, ())
        self.assertEqual(before.experience_information.selections, ())
        self.assertEqual(before.constraints.max_output_characters, 800)

        relation = self.send(restored, "関係確認").relationship_change_record.after
        self.assertEqual(restored.memory.experience_pending_confirmations, pending_experience)
        self.assertEqual(restored.memory.experience_items, ())
        self.sequence += 1
        confirmation = fixtures.raw(f"continuity-{self.sequence}", "経験確認", self.sequence)
        experience = restored.receive_text(confirmation).experience_change_record.after
        memory_snapshot = restored.memory.persistence_snapshot()
        relationship_snapshot = restored.relationship.persistence_snapshot()
        duplicated = restored.receive_text(confirmation)
        self.assertEqual(duplicated.disposition, InputDisposition.DUPLICATE)
        self.assertEqual(restored.memory.persistence_snapshot(), memory_snapshot)
        self.assertEqual(restored.relationship.persistence_snapshot(), relationship_snapshot)
        after = self.send(restored, "コーヒーについて教えて").intelligence_request.proposal
        self.assertEqual(after.relationship_information.selections[0].item, relation)
        self.assertEqual(after.experience_information.selections[0].item, experience)
        self.assertEqual(after.constraints.max_output_characters, 160)


if __name__ == "__main__":
    unittest.main()
