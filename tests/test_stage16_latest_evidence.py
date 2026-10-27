from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import test_stage16_experience_dialogue as fixtures
from yamicha.bootstrap import make_stage16_system
from yamicha.contracts import DecisionDirection, OutputReleaseStatus
from yamicha.contracts.experience import ExperienceChangeStatus


class Stage16LatestEvidenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "latest-evidence.sqlite3"
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

    def send(self, system, text):
        self.sequence += 1
        return system.receive_text(fixtures.raw(
            f"latest-evidence-{self.sequence}", text, self.sequence,
        ))

    def refuse(self, system):
        outcome = self.send(system, "秘密を公開して")
        self.assertEqual(outcome.finalization.finalized_direction, DecisionDirection.REFUSE)
        self.assertEqual(outcome.dialogue_output.status, OutputReleaseStatus.RELEASED)
        return outcome

    def assert_latest_feedback_rejected(self, system):
        rejected = self.send(system, "経験：直前：長すぎた")
        self.assertEqual(rejected.experience_change_record.status, ExperienceChangeStatus.REJECTED)
        self.assertIsNone(rejected.experience_pending_confirmation)
        confirmed = self.send(system, "経験確認")
        self.assertEqual(confirmed.experience_change_record.status, ExperienceChangeStatus.NOT_FOUND)
        self.assertEqual(system.memory.experience_items, ())

    def test_initial_feedback_requires_a_public_response(self):
        self.assert_latest_feedback_rejected(self.make_system())

    def test_refusal_cannot_redirect_latest_feedback_to_an_older_response(self):
        system = self.make_system()
        ordinary = self.send(system, "コーヒーについて教えて")
        self.refuse(system)
        self.assertEqual(system.memory.experience_evidence[-1].source_reference,
                         ordinary.dialogue_output.artifact_id)

        self.assert_latest_feedback_rejected(system)

    def test_restart_preserves_refusal_as_the_latest_public_response(self):
        system = self.make_system()
        self.send(system, "コーヒーについて教えて")
        refused = self.refuse(system)
        system.shutdown()
        restored = self.make_system(require_existing_persistence=True)
        self.assertEqual(restored.relationship.active_dialogue_context.turns[-1].source_reference,
                         refused.dialogue_output.artifact_id)

        self.assert_latest_feedback_rejected(restored)

    def test_a_new_ordinary_response_restores_a_valid_feedback_source(self):
        system = self.make_system()
        older = self.send(system, "コーヒーについて教えて")
        self.refuse(system)
        self.send(system, "経験：直前：長すぎた")
        latest = self.send(system, "紅茶について教えて")
        pending = self.send(system, "経験：直前：長すぎた")
        self.assertEqual(pending.experience_change_record.status, ExperienceChangeStatus.CONFIRMATION_REQUIRED)
        accepted = self.send(system, "経験確認")
        item = accepted.experience_change_record.after
        observation = next(value for value in system.memory.experience_evidence
                           if value.evidence_id == item.evidence_id)
        self.assertEqual(observation.source_reference, latest.dialogue_output.artifact_id)
        self.assertNotEqual(observation.source_reference, older.dialogue_output.artifact_id)

    def test_management_outputs_do_not_replace_the_ordinary_feedback_source(self):
        system = self.make_system()
        ordinary = self.send(system, "おはよう")
        self.send(system, "関係：希望：説明は短めに")
        self.send(system, "関係確認")
        self.send(system, "覚えて：コーヒーが好き")
        self.send(system, "コーヒーを完全に削除して")
        cancelled = self.send(system, "いいえ")
        self.assertIsNotNone(cancelled.conversation_memory_governance_record)

        pending = self.send(system, "経験：直前：長すぎた")
        self.assertEqual(pending.experience_change_record.status, ExperienceChangeStatus.CONFIRMATION_REQUIRED)
        accepted = self.send(system, "経験確認")
        item = accepted.experience_change_record.after
        observation = next(value for value in system.memory.experience_evidence
                           if value.evidence_id == item.evidence_id)
        self.assertEqual(observation.source_reference, ordinary.dialogue_output.artifact_id)

    def test_refusal_does_not_allow_reassessment_from_an_older_response(self):
        system = self.make_system()
        self.send(system, "コーヒーについて教えて")
        self.send(system, "経験：直前：長すぎた")
        original = self.send(system, "経験確認").experience_change_record.after
        self.send(system, "コーヒーをもう一度説明して")
        self.refuse(system)

        rejected = self.send(system, f"経験見直し：{original.item_id}：説明が足りなかった")

        self.assertEqual(rejected.experience_change_record.status, ExperienceChangeStatus.REJECTED)
        self.assertIsNone(rejected.experience_pending_confirmation)
        self.assertEqual(system.memory.experience_items, (original,))

    def test_explicit_capability_result_remains_available_after_a_refusal(self):
        (Path(self.directory.name) / "document.txt").write_text("document", encoding="utf-8")
        system = self.make_system(
            capability_root=Path(self.directory.name),
            capability_authority_id="human-001",
            authorized_read_targets=("document.txt",),
        )
        self.send(system, "ファイルを確認したい")
        self.sequence += 1
        outcome = system.use_read_capability(
            target="document.txt", authority_id="human-001", idempotency_key="latest-evidence-read",
            requested_at=fixtures.raw("time-only", "時刻", self.sequence).received_at,
        )
        self.refuse(system)

        pending = self.send(system, f"経験：能力:{outcome.result.result_id}：長すぎた")

        self.assertEqual(pending.experience_change_record.status, ExperienceChangeStatus.CONFIRMATION_REQUIRED)
        accepted = self.send(system, "経験確認")
        item = accepted.experience_change_record.after
        observation = next(value for value in system.memory.experience_evidence
                           if value.evidence_id == item.evidence_id)
        self.assertEqual(observation.source_reference, outcome.result.result_id)


if __name__ == "__main__":
    unittest.main()
