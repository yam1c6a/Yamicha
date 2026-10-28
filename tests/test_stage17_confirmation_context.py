"""G17 regression: clarification replies retain their verified question context."""

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
from yamicha.contracts import DecisionDirection, DialogueSpeaker, ExternalTime


class Stage17ConfirmationContextTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "confirmation-context.sqlite3"
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
        return system.receive_text(fixtures.raw(
            f"confirmation-context-{self.sequence}", text, self.sequence,
        ))

    def prepare_confirmation(self, system):
        self.send(system, "覚えて：コーヒーはモカを選ぶ")
        self.send(system, "準備できましたか？")
        self.send(system, "経験：直前：確認してから答えてほしかった")
        experience = self.send(system, "経験確認").experience_change_record.after
        question = self.send(system, "コーヒーの淹れ方を説明して")
        self.assertEqual(question.finalization.finalized_direction, DecisionDirection.CONFIRM)
        self.assertTrue(question.conversation_memory_window.selections)
        self.assertIsNone(question.intelligence_request)
        return experience, question

    def assert_question_delivered(self, outcome, question, current_text: str) -> None:
        self.assertEqual(outcome.finalization.finalized_direction, DecisionDirection.RESPOND)
        proposal = outcome.intelligence_request.proposal
        self.assertEqual(proposal.input_text, current_text)
        self.assertEqual(proposal.experience_confirmation_reference, question.dialogue_output.artifact_id)
        turns = tuple(
            turn for turn in proposal.dialogue_context.turns
            if turn.lifecycle_id == question.context.lifecycle_id
        )
        self.assertEqual(tuple(turn.speaker for turn in turns), (
            DialogueSpeaker.HUMAN, DialogueSpeaker.YAMICHA,
        ))
        self.assertEqual(turns[0].text, question.context.event.meaning.normalized_text)
        self.assertEqual(turns[1].text, question.dialogue_output.text)
        self.assertEqual(turns[1].source_reference, proposal.experience_confirmation_reference)
        self.assert_budget(proposal)

    def assert_budget(self, proposal) -> None:
        self.assertEqual(proposal.dialogue_context.current_input_characters, len(proposal.input_text))
        self.assertLessEqual(len({turn.lifecycle_id for turn in proposal.dialogue_context.turns}), 6)
        total = proposal.dialogue_context.total_characters
        for window in (
            proposal.conversation_memory,
            proposal.relationship_information,
            proposal.experience_information,
        ):
            if window is not None:
                total += window.total_characters
        self.assertLessEqual(total, 4096)

    def test_short_answer_delivers_original_question_and_released_confirmation(self) -> None:
        system = self.make_system()
        _, question = self.prepare_confirmation(system)

        answer = self.send(system, "温度です。")

        self.assert_question_delivered(answer, question, "温度です。")
        self.assertEqual(answer.conversation_memory_window.selections, ())
        self.assertTrue(all("モカ" not in turn.text for turn in answer.intelligence_request.proposal.dialogue_context.turns))
        next_question = self.send(system, "紅茶の淹れ方を説明して")
        self.assertEqual(next_question.finalization.finalized_direction, DecisionDirection.CONFIRM)
        self.assertIsNone(next_question.context.memory.experience_confirmation_reference)

    def test_short_answer_preserves_question_context_across_restart(self) -> None:
        system = self.make_system()
        experience, question = self.prepare_confirmation(system)
        system.shutdown()
        restored = self.make_system(require_existing_persistence=True)

        answer = self.send(restored, "温度です。")

        self.assert_question_delivered(answer, question, "温度です。")
        self.assertEqual(answer.experience_information.selections[0].item, experience)

    def test_new_context_does_not_import_previous_clarification(self) -> None:
        system = self.make_system()
        _, question = self.prepare_confirmation(system)
        self.sequence += 1
        system.start_new_dialogue_context(
            ExternalTime(fixtures.NOW.value + timedelta(seconds=self.sequence))
        )

        answer = self.send(system, "温度です。")

        proposal = answer.intelligence_request.proposal
        self.assertIsNone(proposal.experience_confirmation_reference)
        self.assertNotIn(question.context.lifecycle_id, {
            turn.lifecycle_id for turn in proposal.dialogue_context.turns
        })
        self.assertEqual(proposal.dialogue_context.turns, ())

    def test_forgetting_interrupts_clarification_and_does_not_reintroduce_old_question(self) -> None:
        system = self.make_system()
        _, question = self.prepare_confirmation(system)
        self.send(system, "コーヒーを忘れて")

        interrupted = self.send(system, "温度です。")

        self.assertEqual(interrupted.finalization.finalized_direction, DecisionDirection.CONFIRM)
        self.assertIsNone(interrupted.context.memory.experience_confirmation_reference)
        answer = self.send(system, "90度について知りたいです。")
        self.assert_question_delivered(answer, interrupted, "90度について知りたいです。")
        self.assertNotIn(question.context.lifecycle_id, {
            turn.lifecycle_id for turn in answer.intelligence_request.proposal.dialogue_context.turns
        })
        self.assertEqual(answer.conversation_memory_window.selections, ())
        self.assertTrue(all("モカ" not in turn.text for turn in answer.intelligence_request.proposal.dialogue_context.turns))

    def test_revised_experience_uses_only_its_own_confirmation_context(self) -> None:
        system = self.make_system()
        experience, old_question = self.prepare_confirmation(system)
        self.send(system, f"経験見直し：{experience.item_id}：確認してから答えてほしかった")
        revised = self.send(system, "経験確認").experience_change_record.after
        self.assertGreater(revised.version, experience.version)

        question = self.send(system, "温度です。")

        self.assertEqual(question.finalization.finalized_direction, DecisionDirection.CONFIRM)
        self.assertIsNone(question.context.memory.experience_confirmation_reference)
        answer = self.send(system, "90度について知りたいです。")
        self.assert_question_delivered(answer, question, "90度について知りたいです。")
        self.assertNotIn(old_question.context.lifecycle_id, {
            turn.lifecycle_id for turn in answer.intelligence_request.proposal.dialogue_context.turns
        })

    def test_full_current_input_keeps_priority_over_confirmation_history(self) -> None:
        system = self.make_system()
        self.prepare_confirmation(system)
        current_text = "温度は" + "あ" * (4096 - len("温度は"))

        answer = self.send(system, current_text)

        proposal = answer.intelligence_request.proposal
        self.assertEqual(proposal.input_text, current_text)
        self.assertEqual(proposal.dialogue_context.turns, ())
        self.assertIsNone(proposal.experience_confirmation_reference)
        self.assert_budget(proposal)

    def test_insufficient_context_budget_keeps_confirmation_without_llm_call(self) -> None:
        system = self.make_system()
        experience, _ = self.prepare_confirmation(system)
        # The current answer and selected policy fit exactly, but the original
        # question and released clarification do not fit in the same request.
        current_characters = 4096 - len(experience.instruction)
        current_text = "温度は" + "あ" * (current_characters - len("温度は"))

        answer = self.send(system, current_text)

        self.assertEqual(answer.context.event.meaning.normalized_text, current_text)
        self.assertTrue(answer.experience_information.selections)
        self.assertEqual(answer.finalization.finalized_direction, DecisionDirection.CONFIRM)
        self.assertIsNone(answer.context.memory.experience_confirmation_reference)
        self.assertIsNone(answer.intelligence_request)

    def test_expired_relationship_does_not_restore_confirmation_context(self) -> None:
        system = self.make_system()
        self.send(system, "関係：配慮：期間限定の説明 | 話題=コーヒー | 期限=2026-10-27T12:01:00+00:00")
        self.send(system, "関係確認")
        _, question = self.prepare_confirmation(system)
        self.assertTrue(question.relationship_information.selections)
        self.sequence = 61

        answer = self.send(system, "温度です。")

        self.assertEqual(answer.finalization.finalized_direction, DecisionDirection.CONFIRM)
        self.assertIsNone(answer.context.memory.experience_confirmation_reference)
        self.assertIsNone(answer.intelligence_request)

    def test_memory_updated_in_confirmation_exchange_does_not_restore_old_version(self) -> None:
        system = self.make_system()
        self.send(system, "私はコーヒーが好き")
        self.send(system, "経験：直前：確認してから答えてほしかった")
        self.send(system, "経験確認")
        question = self.send(system, "私はコーヒーが好き")
        self.assertEqual(question.finalization.finalized_direction, DecisionDirection.CONFIRM)
        selected = question.conversation_memory_window.selections[0]
        self.assertGreater(system.memory.conversation_memory_items[0].version, selected.memory_item_version)

        answer = self.send(system, "温度です。")

        self.assertEqual(answer.finalization.finalized_direction, DecisionDirection.CONFIRM)
        self.assertIsNone(answer.context.memory.experience_confirmation_reference)
        self.assertIsNone(answer.intelligence_request)

    def test_legacy_confirmation_without_complete_material_trace_is_not_restored(self) -> None:
        system = self.make_system()
        self.prepare_confirmation(system)
        # Restore the pre-G17 record shape through a normal committed snapshot.
        system.core._records = [replace(record, entries=tuple(
            entry for entry in record.entries
            if not entry.source_reference.startswith(("dialogue-materials:", "conversation-memory:", "dialogue-turn:"))
        )) for record in system.core.lifecycle_records]
        system.snapshot_coordinator.commit(ExternalTime(fixtures.NOW.value + timedelta(seconds=self.sequence)))
        system.shutdown()
        restored = self.make_system(require_existing_persistence=True)

        answer = self.send(restored, "温度です。")

        self.assertEqual(answer.finalization.finalized_direction, DecisionDirection.CONFIRM)
        self.assertIsNone(answer.context.memory.experience_confirmation_reference)
        self.assertIsNone(answer.intelligence_request)


if __name__ == "__main__":
    unittest.main()
