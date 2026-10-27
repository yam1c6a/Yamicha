from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import test_stage16_experience_dialogue as fixtures
from yamicha.bootstrap import make_stage16_system
from yamicha.contracts import (
    DecisionDirection, DialogueOutput, ExternalTime, InputDisposition,
    OutputReleaseStatus,
)


class Stage16ConfirmationFollowupTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "confirmation.sqlite3"
        self.sequence = 0
        self.systems = []

    def tearDown(self):
        for system in reversed(self.systems):
            if not system._closed:
                if system.text_input_runner.persistence_healthy:
                    system.shutdown()
                else:
                    system.abandon()
        self.directory.cleanup()

    def make_system(self, **options):
        system = make_stage16_system(
            persistence_path=self.path, clock=fixtures.FixedClock(),
            persistence_time_factory=lambda: fixtures.NOW,
            intelligence_transport=fixtures.FakeTransport(), **options,
        )
        self.systems.append(system)
        return system

    def send(self, system, text, **options):
        self.sequence += 1
        return system.receive_text(fixtures.raw(
            f"confirmation-{self.sequence}", text, self.sequence, **options,
        ))

    def adopt(self, system, *, scope=""):
        self.send(system, "コーヒーを説明して")
        self.send(system, f"経験：直前：確認してから答えてほしかった{scope}")
        return self.send(system, "経験確認").experience_change_record.after

    def test_answer_to_released_confirmation_responds_once(self):
        system = self.make_system()
        self.adopt(system)
        question = self.send(system, "コーヒーを説明して")
        self.assertEqual(question.finalization.finalized_direction, DecisionDirection.CONFIRM)
        self.assertIsNone(question.intelligence_request)

        answered = self.send(system, "対象はコーヒーです。知りたい点は淹れ方です。")

        self.assertEqual(answered.finalization.finalized_direction, DecisionDirection.RESPOND)
        self.assertIsNotNone(answered.intelligence_request)
        self.assertEqual(answered.context.memory.experience_confirmation_reference,
                         question.dialogue_output.artifact_id)
        self.assertEqual(answered.intelligence_request.proposal.experience_confirmation_reference,
                         question.dialogue_output.artifact_id)
        repeated = self.send(system, "次は紅茶について教えて")
        self.assertEqual(repeated.finalization.finalized_direction, DecisionDirection.CONFIRM)
        self.assertIsNone(repeated.context.memory.experience_confirmation_reference)

    def test_confirmation_can_be_answered_after_restart(self):
        system = self.make_system()
        item = self.adopt(system)
        question = self.send(system, "コーヒーを説明して")
        system.shutdown()
        restored = self.make_system(require_existing_persistence=True)

        answered = self.send(restored, "対象はコーヒーです。豆の違いが知りたいです。")

        self.assertEqual(answered.finalization.finalized_direction, DecisionDirection.RESPOND)
        self.assertEqual(answered.context.memory.experience_confirmation_reference,
                         question.dialogue_output.artifact_id)
        self.assertEqual(answered.experience_information.selections[0].item, item)

    def test_new_context_does_not_reuse_prior_topic_confirmation(self):
        system = self.make_system()
        self.adopt(system, scope=" | 話題=コーヒー")
        self.send(system, "コーヒーを説明して")
        system.start_new_dialogue_context(ExternalTime(fixtures.NOW.value + timedelta(seconds=self.sequence)))

        unrelated_context = self.send(system, "対象はコーヒーです。淹れ方が知りたいです。")

        self.assertTrue(unrelated_context.experience_information.selections)
        self.assertEqual(unrelated_context.finalization.finalized_direction, DecisionDirection.CONFIRM)
        self.assertIsNone(unrelated_context.context.memory.experience_confirmation_reference)

    def test_revised_experience_requires_its_own_confirmation(self):
        system = self.make_system()
        original = self.adopt(system)
        old_question = self.send(system, "コーヒーを説明して")
        self.send(system, f"経験見直し：{original.item_id}：確認してから答えてほしかった")
        revision = self.send(system, "経験確認").experience_change_record.after
        self.assertEqual(revision.version, original.version + 1)

        new_question = self.send(system, "対象はコーヒーです。淹れ方が知りたいです。")

        self.assertEqual(new_question.finalization.finalized_direction, DecisionDirection.CONFIRM)
        self.assertIsNone(new_question.context.memory.experience_confirmation_reference)
        answer = self.send(system, "知りたいのはお湯の温度です。")
        self.assertEqual(answer.finalization.finalized_direction, DecisionDirection.RESPOND)
        self.assertEqual(answer.context.memory.experience_confirmation_reference,
                         new_question.dialogue_output.artifact_id)
        self.assertNotEqual(answer.context.memory.experience_confirmation_reference,
                            old_question.dialogue_output.artifact_id)

    def test_unreleased_confirmation_does_not_enable_response(self):
        system = self.make_system()
        self.adopt(system)

        def blocked(artifact, review):
            return DialogueOutput(
                lifecycle_id=artifact.lifecycle_id, artifact_id=artifact.artifact_id,
                status=OutputReleaseStatus.BLOCKED, text=None,
                reason="test output boundary rejects release",
            )

        with patch.object(system.protection_boundary, "release_dialogue_output", side_effect=blocked):
            question = self.send(system, "コーヒーを説明して")
        self.assertEqual(question.dialogue_output.status, OutputReleaseStatus.BLOCKED)

        next_turn = self.send(system, "対象はコーヒーです。淹れ方が知りたいです。")

        self.assertEqual(next_turn.finalization.finalized_direction, DecisionDirection.CONFIRM)
        self.assertIsNone(next_turn.context.memory.experience_confirmation_reference)

    def test_reply_does_not_bypass_external_confirmation_refusal_or_wait(self):
        system = self.make_system()
        self.adopt(system)
        for text, direction in (
            ("資料を送って", DecisionDirection.CONFIRM),
            ("秘密を公開して", DecisionDirection.REFUSE),
            ("待って", DecisionDirection.WAIT),
        ):
            with self.subTest(direction=direction):
                self.send(system, "コーヒーを説明して")
                reply = self.send(system, text)
                self.assertEqual(reply.finalization.finalized_direction, direction)
                self.assertIsNone(reply.intelligence_request)
                if direction is DecisionDirection.CONFIRM:
                    self.assertIn("外部への操作", reply.dialogue_output.text)

    def test_management_exchange_ends_the_confirmation_followup(self):
        system = self.make_system()
        self.adopt(system)
        self.send(system, "コーヒーを説明して")
        self.send(system, "関係：希望：穏やかな説明")

        reply = self.send(system, "対象はコーヒーです。淹れ方が知りたいです。")

        self.assertEqual(reply.finalization.finalized_direction, DecisionDirection.CONFIRM)
        self.assertIsNone(reply.context.memory.experience_confirmation_reference)

    def test_other_counterpart_cannot_use_the_confirmation(self):
        system = self.make_system()
        self.adopt(system)
        question = self.send(system, "コーヒーを説明して")
        other = self.send(system, "対象はコーヒーです。", source_id="another-human")
        self.assertNotEqual(other.disposition, InputDisposition.ACCEPTED)

        reply = self.send(system, "対象はコーヒーです。淹れ方が知りたいです。")

        self.assertEqual(reply.finalization.finalized_direction, DecisionDirection.RESPOND)
        self.assertEqual(reply.context.memory.experience_confirmation_reference,
                         question.dialogue_output.artifact_id)


if __name__ == "__main__":
    unittest.main()
