from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import patch


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from yamicha.body.persistence import (  # noqa: E402
    PersistenceCommitError,
    PersistenceConsistencyError,
)
from yamicha.bootstrap import (  # noqa: E402
    InteractiveConsole,
    make_stage13_system,
    make_stage14_system,
    make_stage15_system,
    make_stage16_system,
)
from yamicha.contracts import (  # noqa: E402
    CapabilityDispatchStatus,
    CapabilityResultStatus,
    ClockObservation,
    DecisionDirection,
    ExternalIntelligenceResponse,
    ExternalTime,
    InputDisposition,
    IntelligenceResultStatus,
    MonotonicTime,
    ProtectionAuditKind,
    ProtectionDecision,
    ProtectionMode,
    RawTextInput,
    ReadOnlyToolResult,
    SourceVerification,
)
from yamicha.contracts.experience import (  # noqa: E402
    ExperienceChangeStatus,
    ExperienceEvidenceKind,
    ExperienceTarget,
    ExperienceValue,
)


NOW = ExternalTime(datetime(2026, 10, 27, 12, 0, tzinfo=UTC))


class FixedClock:
    def observe(self) -> ClockObservation:
        return ClockObservation(external=NOW, monotonic=MonotonicTime(100.0))


class FakeTransport:
    def __init__(self, *, unavailable: bool = False, reply: str = "応答") -> None:
        self.requests = []
        self.unavailable = unavailable
        self.reply = reply

    def generate(self, request):
        self.requests.append(request)
        return ExternalIntelligenceResponse(
            status=(IntelligenceResultStatus.UNAVAILABLE if self.unavailable else IntelligenceResultStatus.SUCCESS),
            model=request.proposal.model,
            content=None if self.unavailable else json.dumps({"reply": self.reply}),
            detail="stage-16 deterministic test transport",
        )


class UnknownResultReader:
    def read(self, target: str) -> ReadOnlyToolResult:
        return ReadOnlyToolResult(
            status=CapabilityResultStatus.UNKNOWN,
            content=None,
            observed_scope=target,
            remaining_scope=None,
            detail="結果を確認できなかった",
            uncertainty="読み取りが完了したか不明",
        )


def raw(input_id: str, text: str, seconds: int = 0, *, source_id: str = "human-001"):
    return RawTextInput(
        input_id=input_id,
        received_at=ExternalTime(NOW.value + timedelta(seconds=seconds)),
        source_id=source_id,
        content=text,
        source_verification=SourceVerification.VERIFIED,
    )


class Stage16ExperienceDialogueTest(unittest.TestCase):
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

    def make_system(self, transport=None, *, factory=make_stage16_system, **options):
        system = factory(
            persistence_path=self.path,
            clock=FixedClock(),
            persistence_time_factory=lambda: NOW,
            intelligence_transport=transport or FakeTransport(),
            **options,
        )
        self.systems.append(system)
        return system

    def send(self, system, text: str, **options):
        self.sequence += 1
        return system.receive_text(raw(f"experience-input-{self.sequence:03}", text, self.sequence, **options))

    def adopt(self, system, reaction: str = "長すぎた", *, scope: str = ""):
        self.send(system, "コーヒーの説明をお願い")
        requested = self.send(system, f"経験：直前：{reaction}{scope}")
        self.assertEqual(requested.experience_change_record.status, ExperienceChangeStatus.CONFIRMATION_REQUIRED)
        confirmed = self.send(system, "経験確認")
        self.assertEqual(confirmed.experience_change_record.status, ExperienceChangeStatus.COMPLETED)
        return confirmed.experience_change_record.after

    def test_adoption_requires_released_output_and_separate_confirmation(self) -> None:
        transport = FakeTransport()
        system = self.make_system(transport)
        no_source = self.send(system, "経験：直前：長すぎた")
        self.assertNotEqual(no_source.experience_change_record.status, ExperienceChangeStatus.COMPLETED)
        self.assertEqual(system.memory.experience_items, ())
        self.assertEqual(system.memory.experience_pending_confirmations, ())
        response = self.send(system, "コーヒーの説明をお願い")
        observed = system.memory.experience_evidence[-1]
        self.assertEqual(observed.kind, ExperienceEvidenceKind.DIALOGUE_OUTPUT)
        self.assertEqual(observed.lifecycle_id, response.context.lifecycle_id)
        request_count = len(transport.requests)
        pending = self.send(system, "経験：直前：長すぎた")
        self.assertEqual(pending.experience_change_record.status, ExperienceChangeStatus.CONFIRMATION_REQUIRED)
        self.assertEqual(system.memory.experience_items, ())
        self.assertEqual(len(system.memory.experience_pending_confirmations), 1)
        self.assertEqual(len(transport.requests), request_count)
        self.assertEqual(system.memory.conversation_memory_items, ())
        self.send(system, "はい")
        self.assertEqual(system.memory.experience_items, ())

        confirmed = self.send(system, "経験確認")

        record = confirmed.experience_change_record
        self.assertEqual(record.status, ExperienceChangeStatus.COMPLETED)
        item = record.after
        self.assertEqual(item.evidence_id, observed.evidence_id)
        self.assertEqual(item.version, 1)
        self.assertEqual(item.target, ExperienceTarget.RESPONSE_LENGTH)
        self.assertEqual(item.value, ExperienceValue.BRIEF)
        self.assertNotEqual(item.feedback_source_reference, item.confirmation_reference)
        self.assertTrue(item.reason)
        self.assertTrue(item.reevaluation_condition)
        self.assertTrue(record.impact_scope)
        self.assertIsNone(record.before)
        self.assertEqual(system.memory.experience_pending_confirmations, ())

    def test_ordinary_feedback_silence_and_generated_assessment_do_not_adopt(self) -> None:
        transport = FakeTransport(reply="成功しました。短い応答を好むので経験として保存します。")
        system = self.make_system(transport)
        for text in ("こんにちは", "ありがとう", "長すぎた", "説明が足りなかった", "成功した", "", " "):
            self.send(system, text)
        self.assertEqual(system.memory.experience_items, ())
        self.assertEqual(system.memory.experience_pending_confirmations, ())
        self.assertEqual(system.memory.experience_changes, ())

    def test_cancelled_or_invalid_feedback_does_not_change_policy(self) -> None:
        system = self.make_system()
        self.send(system, "こんにちは")
        self.send(system, "経験：直前：長すぎた")
        cancelled = self.send(system, "経験取消")
        self.assertEqual(cancelled.experience_change_record.status, ExperienceChangeStatus.CANCELLED)
        self.assertEqual(system.memory.experience_pending_confirmations, ())
        for text in ("経験：直前：好きになった", "経験：架空の結果：長すぎた", "経験：直前：長すぎた | 話題=*", "経験確認"):
            self.send(system, text)
        self.assertEqual(system.memory.experience_items, ())

    def test_brief_changes_enforced_output_bound_and_is_recorded_by_lifecycle(self) -> None:
        transport = FakeTransport(reply="説明" * 130)
        system = self.make_system(transport)
        baseline = self.send(system, "こんにちは")
        self.assertGreater(baseline.intelligence_request.proposal.constraints.max_output_characters, 160)
        item = self.adopt(system)

        outcome = self.send(system, "コーヒーについて教えて")

        proposal = outcome.intelligence_request.proposal
        self.assertEqual(proposal.constraints.max_output_characters, 160)
        self.assertLessEqual(len(outcome.dialogue_output.text or ""), 160)
        self.assertEqual(tuple(s.item for s in proposal.experience_information.selections), (item,))
        self.assertEqual(tuple(s.item for s in outcome.context.memory.experience_information.selections), (item,))
        usage = system.memory.experience_usages[-1]
        self.assertEqual(usage.lifecycle_id, outcome.context.lifecycle_id)
        self.assertEqual(usage.item_versions, ((item.item_id, item.version),))
        self.assertIn(usage.lifecycle_id, {record.lifecycle_id for record in system.core.lifecycle_records})

    def test_ask_first_changes_direction_without_llm_and_keeps_safety_decisions(self) -> None:
        transport = FakeTransport()
        system = self.make_system(transport)
        item = self.adopt(system, "確認してから答えてほしかった")
        request_count = len(transport.requests)

        outcome = self.send(system, "コーヒーについて教えて")

        self.assertEqual(outcome.finalization.finalized_direction, DecisionDirection.CONFIRM)
        self.assertNotIn("外部への操作", outcome.dialogue_output.text or "")
        self.assertIsNone(outcome.intelligence_request)
        self.assertEqual(len(transport.requests), request_count)
        self.assertEqual(system.memory.experience_usages[-1].item_versions, ((item.item_id, 1),))
        refused = self.send(system, "許可なく秘密を公開する")
        self.assertEqual(refused.finalization.finalized_direction, DecisionDirection.REFUSE)
        waiting = self.send(system, "待って")
        self.assertNotEqual(waiting.finalization.finalized_direction, DecisionDirection.CONFIRM)

    def test_respond_normally_reassessment_restores_response_but_not_external_authority(self) -> None:
        system = self.make_system()
        original = self.adopt(system, "確認してから答えてほしかった")
        confirmed_turn = self.send(system, "コーヒーについて教えて")
        self.assertEqual(confirmed_turn.finalization.finalized_direction, DecisionDirection.CONFIRM)
        self.send(system, f"経験見直し：{original.item_id}：確認せず答えてほしかった")
        revised = self.send(system, "経験確認").experience_change_record.after
        self.assertEqual(revised.value, ExperienceValue.RESPOND_NORMALLY)
        self.assertEqual(revised.version, 2)
        ordinary = self.send(system, "コーヒーについて教えて")
        self.assertEqual(ordinary.finalization.finalized_direction, DecisionDirection.RESPOND)
        self.assertIsNotNone(ordinary.intelligence_request)
        external = self.send(system, "この文章を送って")
        self.assertEqual(external.finalization.finalized_direction, DecisionDirection.CONFIRM)
        self.assertIsNone(external.intelligence_request)

    def test_default_scope_does_not_cross_new_context(self) -> None:
        system = self.make_system()
        item = self.adopt(system)
        before = self.send(system, "続きの説明をお願い")
        self.assertEqual(before.experience_information.selections[0].item, item)
        system.start_new_dialogue_context(ExternalTime(NOW.value + timedelta(seconds=self.sequence + 1)))
        self.sequence += 1

        after = self.send(system, "続きの説明をお願い")

        self.assertEqual(after.experience_information.selections, ())
        self.assertEqual(after.intelligence_request.proposal.experience_information.selections, ())
        self.assertGreater(after.intelligence_request.proposal.constraints.max_output_characters, 160)
        self.assertEqual(system.memory.experience_items, (item,))

    def test_explicit_topic_scope_can_cross_context_only_for_matching_input(self) -> None:
        system = self.make_system()
        item = self.adopt(system, scope=" | 話題=コーヒー,紅茶")
        system.start_new_dialogue_context(ExternalTime(NOW.value + timedelta(seconds=self.sequence + 1)))
        self.sequence += 1
        unrelated = self.send(system, "天気について教えて")
        self.assertEqual(unrelated.experience_information.selections, ())
        related = self.send(system, "紅茶について教えて")
        self.assertEqual(related.experience_information.selections[0].item, item)
        self.assertEqual(related.intelligence_request.proposal.constraints.max_output_characters, 160)

    def test_other_counterpart_and_duplicate_input_cannot_confirm(self) -> None:
        system = self.make_system()
        self.send(system, "こんにちは")
        original = raw("same-experience-input", "経験：直前：長すぎた", 2)
        system.receive_text(original)
        before = system.memory.persistence_snapshot()
        duplicate = system.receive_text(original)
        self.assertEqual(duplicate.disposition, InputDisposition.DUPLICATE)
        self.assertEqual(system.memory.persistence_snapshot(), before)
        other = self.send(system, "経験確認", source_id="another-human")
        self.assertNotEqual(other.disposition, InputDisposition.ACCEPTED)
        self.assertEqual(system.memory.experience_items, ())
        self.assertEqual(system.memory.experience_pending_confirmations, before.experience_pending_confirmations)

    def test_confirmation_expiry_rejects_pending(self) -> None:
        system = self.make_system()
        self.send(system, "こんにちは")
        self.send(system, "経験：直前：長すぎた")

        expired = system.receive_text(raw("late-experience-confirm", "経験確認", 86403))

        self.assertEqual(expired.experience_change_record.status, ExperienceChangeStatus.STALE)
        self.assertEqual(system.memory.experience_items, ())

    def test_reassessment_requires_new_observation_and_preserves_before_after(self) -> None:
        transport = FakeTransport(reply="最初の応答")
        system = self.make_system(transport)
        original = self.adopt(system)
        stale = self.send(system, f"経験見直し：{original.item_id}：説明が足りなかった")
        self.assertNotEqual(stale.experience_change_record.status, ExperienceChangeStatus.CONFIRMATION_REQUIRED)
        self.assertEqual(system.memory.experience_items, (original,))
        transport.reply = "短い版に依存した旧応答"
        self.send(system, "コーヒーについてもう一度")
        requested = self.send(system, f"経験見直し：{original.item_id}：説明が足りなかった")
        self.assertEqual(requested.experience_change_record.status, ExperienceChangeStatus.CONFIRMATION_REQUIRED)
        self.assertEqual(system.memory.experience_items, (original,))

        confirmed = self.send(system, "経験確認")

        record = confirmed.experience_change_record
        revised = record.after
        self.assertEqual(record.before, original)
        self.assertEqual(revised.item_id, original.item_id)
        self.assertEqual(revised.version, 2)
        self.assertEqual(revised.value, ExperienceValue.DETAILED)
        self.assertNotEqual(revised.evidence_id, original.evidence_id)
        self.assertTrue(record.reason)
        self.assertTrue(record.impact_scope)
        transport.reply = "詳しい新応答"
        following = self.send(system, "コーヒーを詳しく説明して")
        proposal = following.intelligence_request.proposal
        self.assertEqual(tuple(s.item for s in proposal.experience_information.selections), (revised,))
        self.assertEqual(proposal.constraints.max_output_characters, 800)
        self.assertTrue(all("短い版に依存した旧応答" not in turn.text for turn in proposal.dialogue_context.turns))
        self.assertEqual(system.memory.experience_usages[-1].item_versions, ((original.item_id, 2),))

    def test_inactivation_requires_confirmation_and_excludes_old_dialogue(self) -> None:
        transport = FakeTransport(reply="通常応答")
        system = self.make_system(transport)
        original = self.adopt(system)
        transport.reply = "不採用前の経験依存応答"
        self.send(system, "コーヒーの続き")
        requested = self.send(system, f"経験不採用：{original.item_id}")
        self.assertEqual(requested.experience_change_record.status, ExperienceChangeStatus.CONFIRMATION_REQUIRED)
        self.assertTrue(system.memory.experience_items[0].active)
        confirmed = self.send(system, "経験確認")
        self.assertEqual(confirmed.experience_change_record.before, original)
        inactive = confirmed.experience_change_record.after
        self.assertFalse(inactive.active)
        self.assertEqual(inactive.version, 2)
        transport.reply = "経験に依存しない応答"

        proposal = self.send(system, "コーヒーについて").intelligence_request.proposal

        self.assertEqual(proposal.experience_information.selections, ())
        self.assertTrue(all("不採用前の経験依存応答" not in turn.text for turn in proposal.dialogue_context.turns))

    def test_topic_mismatch_excludes_prior_experience_dependent_dialogue(self) -> None:
        transport = FakeTransport()
        system = self.make_system(transport)
        self.adopt(system, scope=" | 話題=コーヒー")
        transport.reply = "コーヒー経験に依存した応答"
        self.send(system, "コーヒーについて")
        transport.reply = "天気への応答"

        proposal = self.send(system, "天気について").intelligence_request.proposal

        self.assertEqual(proposal.experience_information.selections, ())
        self.assertTrue(all("コーヒー経験に依存した応答" not in turn.text for turn in proposal.dialogue_context.turns))

    def test_pending_and_adopted_histories_survive_restart_and_model_change(self) -> None:
        first = self.make_system()
        self.send(first, "こんにちは")
        self.send(first, "経験：直前：長すぎた")
        pending_snapshot = first.memory.persistence_snapshot()
        identity = first.recovery.identity.subject_id
        first.shutdown()
        restored = self.make_system(require_existing_persistence=True, intelligence_model="replacement-local-model")
        self.assertEqual(restored.recovery.identity.subject_id, identity)
        self.assertEqual(restored.memory.persistence_snapshot(), pending_snapshot)
        self.send(restored, "経験確認")
        item = restored.memory.experience_items[0]
        used = self.send(restored, "続きの説明をお願い")
        self.assertEqual(used.intelligence_request.proposal.model, "replacement-local-model")
        self.assertEqual(used.experience_information.selections[0].item, item)
        adopted_snapshot = restored.memory.persistence_snapshot()
        restored.shutdown()
        reopened = self.make_system(require_existing_persistence=True)
        self.assertEqual(reopened.memory.persistence_snapshot(), adopted_snapshot)

    def test_unavailable_llm_still_allows_explicit_experience_confirmation(self) -> None:
        system = self.make_system(FakeTransport(unavailable=True))
        item = self.adopt(system)
        outcome = self.send(system, "説明をお願い")
        self.assertEqual(outcome.experience_information.selections[0].item, item)
        self.assertLessEqual(len(outcome.dialogue_output.text or ""), 160)

    def test_experience_relationship_and_memory_confirmations_are_separate(self) -> None:
        system = self.make_system()
        self.send(system, "覚えて：コーヒーが好き")
        self.send(system, "コーヒーを完全に削除して")
        self.send(system, "関係：希望：説明は短めに")
        self.send(system, "こんにちは")
        self.send(system, "経験：直前：長すぎた")
        self.send(system, "関係確認")
        self.assertEqual(len(system.relationship.relationship_items), 1)
        self.assertEqual(system.memory.experience_items, ())
        self.assertEqual(len(system.memory.conversation_memory_items), 1)
        self.send(system, "経験確認")
        self.assertEqual(len(system.memory.experience_items), 1)
        self.assertEqual(len(system.memory.conversation_memory_items), 1)
        self.send(system, "はい")
        self.assertTrue(system.memory.conversation_memory_items[0].physically_deleted)
        self.assertNotIn("コーヒー", system.memory.conversation_memory_items[0].meaning)

    def test_actual_memory_confirmation_is_not_experience_evidence(self) -> None:
        system = self.make_system()
        self.send(system, "覚えて：コーヒーが好き")
        self.send(system, "コーヒーを完全に削除して")
        confirmed = self.send(system, "はい")
        self.assertTrue(system.memory.conversation_memory_items[0].physically_deleted)
        self.assertNotIn(confirmed.context.lifecycle_id, {e.lifecycle_id for e in system.memory.experience_evidence})
        requested = self.send(system, "経験：直前：長すぎた")
        self.assertNotEqual(requested.experience_change_record.status, ExperienceChangeStatus.CONFIRMATION_REQUIRED)
        following = self.send(system, "こんにちは")
        self.assertNotIn(confirmed.context.lifecycle_id, {turn.lifecycle_id for turn in following.intelligence_request.proposal.dialogue_context.turns})

    def test_failed_commit_restores_last_committed_experience_on_restart(self) -> None:
        system = self.make_system()
        self.send(system, "こんにちは")
        self.send(system, "経験：直前：長すぎた")
        committed = system.memory.persistence_snapshot()
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("CREATE TRIGGER reject_stage16_checkpoint BEFORE INSERT ON checkpoints BEGIN SELECT RAISE(ABORT, 'simulated interruption'); END")
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(PersistenceCommitError):
            self.send(system, "経験確認")
        with self.assertRaises(RuntimeError):
            self.send(system, "こんにちは")
        system.abandon()
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("DROP TRIGGER reject_stage16_checkpoint")
            connection.commit()
        finally:
            connection.close()
        restored = self.make_system(require_existing_persistence=True)
        self.assertEqual(restored.memory.persistence_snapshot(), committed)
        self.send(restored, "経験確認")
        self.assertEqual(len(restored.memory.experience_items), 1)

    def _assert_upgrade(self, factory) -> None:
        previous = self.make_system(factory=factory)
        self.send(previous, "覚えて：猫が好き")
        if factory is make_stage15_system:
            self.send(previous, "関係：希望：ゆっくり説明してほしい")
            self.send(previous, "関係確認")
        identity = previous.recovery.identity.subject_id
        memories = previous.memory.conversation_memory_items
        context = previous.relationship.active_dialogue_context
        records = previous.core.lifecycle_records
        relationships = previous.relationship.persistence_snapshot()
        previous.activate_fixed_protection(NOW)
        previous.shutdown()

        upgraded = self.make_system(require_existing_persistence=True)

        self.assertEqual(upgraded.recovery.identity.subject_id, identity)
        self.assertEqual(upgraded.recovery.identity.configuration_version, "stage16-v1")
        self.assertEqual(upgraded.memory.conversation_memory_items, memories)
        self.assertEqual(upgraded.relationship.active_dialogue_context, context)
        self.assertEqual(upgraded.relationship.persistence_snapshot(), relationships)
        self.assertEqual(upgraded.core.lifecycle_records, records)
        self.assertEqual(upgraded.memory.experience_items, ())
        self.assertEqual(upgraded.memory.experience_evidence, ())
        self.assertEqual(upgraded.memory.experience_changes, ())
        self.assertEqual(upgraded.memory.experience_pending_confirmations, ())
        self.assertEqual(upgraded.memory.experience_usages, ())
        self.assertEqual(upgraded.protection_boundary.mode, ProtectionMode.PROTECTED)
        self.assertEqual(self.send(upgraded, "こんにちは").disposition, InputDisposition.BLOCKED)

    def test_stage13_migrates_directly_without_inventing_experience(self) -> None:
        self._assert_upgrade(make_stage13_system)

    def test_stage14_migrates_directly_without_inventing_experience(self) -> None:
        self._assert_upgrade(make_stage14_system)

    def test_stage15_migrates_directly_and_preserves_relationships(self) -> None:
        self._assert_upgrade(make_stage15_system)

    def test_unknown_configuration_is_rejected_without_rewriting_identity(self) -> None:
        previous = self.make_system(factory=make_stage13_system, _configuration_version="unsupported-future-v1")
        self.send(previous, "覚えて：猫が好き")
        identity = previous.recovery.identity.subject_id
        previous.shutdown()
        with self.assertRaises(PersistenceConsistencyError):
            self.make_system(require_existing_persistence=True)
        connection = sqlite3.connect(self.path)
        try:
            self.assertEqual(connection.execute("SELECT subject_id, configuration_version FROM identity WHERE singleton = 1").fetchone(), (identity, "unsupported-future-v1"))
        finally:
            connection.close()

    def test_console_displays_real_confirmation_and_experience_id(self) -> None:
        system = self.make_system()
        output = StringIO()
        times = iter(ExternalTime(NOW.value + timedelta(seconds=n)) for n in range(20))
        console = InteractiveConsole(
            input_stream=StringIO("こんにちは\n経験：直前：長すぎた\n/experience\n経験確認\n/experience\n/quit\n"),
            output_stream=output,
            system=system,
            external_time_factory=times.__next__,
            source_id="human-001",
        )

        self.assertEqual(console.run(), 0)

        shown = output.getvalue()
        self.assertIn("Yamicha 対話コンソール（段階16）", shown)
        self.assertIn("経験確認", shown)
        self.assertIn("経験取消", shown)
        self.assertEqual(len(system.memory.experience_items), 1)
        self.assertIn(system.memory.experience_items[0].item_id, shown)
        self.assertEqual(system.sensation.reception_count, 3)

    def _check_experience_lists_blocked(self, system) -> None:
        output = StringIO()
        received = system.sensation.reception_count
        sequence = system.persistence.latest_sequence
        console = InteractiveConsole(
            input_stream=StringIO("/experience\n/experience all\n/quit\n"),
            output_stream=output,
            system=system,
            external_time_factory=lambda: ExternalTime(NOW.value + timedelta(seconds=60)),
            source_id="human-001",
        )
        with patch.object(type(system), "shutdown"):
            self.assertEqual(console.run(), 0)
        shown = output.getvalue()
        for item in system.memory.experience_items:
            self.assertNotIn(item.item_id, shown)
            self.assertNotIn(item.instruction, shown)
        for pending in system.memory.experience_pending_confirmations:
            self.assertNotIn(pending.pending_id, shown)
        self.assertEqual(system.sensation.reception_count, received)
        self.assertEqual(system.persistence.latest_sequence, sequence)
        audit = tuple(record for record in system.persistence.protection_audit_records() if record.target == "owned-information-display")
        self.assertEqual(len(audit), 2)
        self.assertTrue(all(record.kind is ProtectionAuditKind.PERMISSION and record.decision is ProtectionDecision.REJECTED for record in audit))

    def _seed_experience_lists(self):
        system = self.make_system()
        item = self.adopt(system)
        self.send(system, f"経験不採用：{item.item_id}")
        self.send(system, "経験確認")
        self.adopt(system, "説明が足りなかった")
        self.send(system, "もう一度説明して")
        self.send(system, "経験：直前：確認してから答えてほしかった")
        return system

    def test_protection_blocks_active_inactive_and_pending_experience_lists(self) -> None:
        system = self._seed_experience_lists()
        system.activate_fixed_protection(ExternalTime(NOW.value + timedelta(seconds=30)))
        self._check_experience_lists_blocked(system)
        self.assertEqual(system.protection_boundary.mode, ProtectionMode.PROTECTED)

    def test_restored_protection_still_blocks_experience_lists(self) -> None:
        first = self._seed_experience_lists()
        snapshot = first.memory.persistence_snapshot()
        first.activate_fixed_protection(ExternalTime(NOW.value + timedelta(seconds=30)))
        first.shutdown()
        restored = self.make_system(require_existing_persistence=True)
        self._check_experience_lists_blocked(restored)
        self.assertEqual(restored.memory.persistence_snapshot(), snapshot)
        self.assertEqual(restored.protection_boundary.mode, ProtectionMode.PROTECTED)

    def test_disabled_normal_output_blocks_experience_lists(self) -> None:
        system, _item = self._adopt_capability_experience(normal_dialogue_output_enabled=False)
        self._check_experience_lists_blocked(system)
        self.assertEqual(system.protection_boundary.mode, ProtectionMode.NORMAL)

    def test_disabled_output_blocks_management_result_while_change_is_committed(self) -> None:
        system, item = self._adopt_capability_experience(normal_dialogue_output_enabled=False)
        output = StringIO()
        times = iter(ExternalTime(NOW.value + timedelta(seconds=n)) for n in range(10, 20))
        console = InteractiveConsole(
            input_stream=StringIO(f"経験不採用：{item.item_id}\n経験確認\n/quit\n"),
            output_stream=output,
            system=system,
            external_time_factory=times.__next__,
            source_id="human-001",
        )

        self.assertEqual(console.run(), 0)

        shown = output.getvalue()
        self.assertNotIn(item.item_id, shown)
        self.assertNotIn(item.instruction, shown)
        self.assertFalse(system.memory.experience_items[0].active)
        self.assertIn("保護状態または出力権限により内容を表示できません", shown)

    def _adopt_capability_experience(self, **options):
        (Path(self.directory.name) / "document.txt").write_text("実際に読んだ内容", encoding="utf-8")
        system = self.make_system(
            capability_root=Path(self.directory.name),
            capability_authority_id="human-001",
            authorized_read_targets=("document.txt",),
            **options,
        )
        self.send(system, "document.txtについて相談したい")
        self.sequence += 1
        executed = system.use_read_capability(
            target="document.txt",
            authority_id="human-001",
            idempotency_key="experience-read-for-display",
            requested_at=ExternalTime(NOW.value + timedelta(seconds=self.sequence)),
        )
        self.send(system, f"経験：能力:{executed.result.result_id}：長すぎた")
        self.send(system, "経験確認")
        return system, system.memory.experience_items[0]

    def test_blocked_response_cannot_be_used_as_experience_evidence(self) -> None:
        system = self.make_system(normal_dialogue_output_enabled=False)
        self.send(system, "こんにちは")
        self.assertEqual(system.memory.experience_evidence, ())
        self.send(system, "経験：直前：長すぎた")
        self.send(system, "経験確認")
        self.assertEqual(system.memory.experience_items, ())
        self.assertEqual(system.memory.experience_pending_confirmations, ())

    def test_capability_evidence_retains_actual_result_and_limits_dialogue_scope(self) -> None:
        (Path(self.directory.name) / "document.txt").write_text("実際に読んだ内容", encoding="utf-8")
        system = self.make_system(
            capability_root=Path(self.directory.name),
            capability_authority_id="human-001",
            authorized_read_targets=("document.txt",),
        )
        self.send(system, "document.txtについて相談したい")
        self.sequence += 1
        executed = system.use_read_capability(
            target="document.txt",
            authority_id="human-001",
            idempotency_key="experience-read-001",
            requested_at=ExternalTime(NOW.value + timedelta(seconds=self.sequence)),
        )
        self.assertEqual(executed.dispatch_status, CapabilityDispatchStatus.EXECUTED)
        evidence = next(e for e in system.memory.experience_evidence if e.kind is ExperienceEvidenceKind.CAPABILITY_RESULT)
        self.assertEqual(evidence.source_reference, executed.result.result_id)
        self.assertEqual(evidence.capability_status, CapabilityResultStatus.SUCCESS)
        self.assertEqual(evidence.capability_target, "document.txt")
        requested = self.send(system, f"経験：能力:{executed.result.result_id}：長すぎた")
        self.assertEqual(requested.experience_change_record.status, ExperienceChangeStatus.CONFIRMATION_REQUIRED)
        self.send(system, "経験確認")
        item = system.memory.experience_items[0]
        self.assertEqual(item.capability_target, "document.txt")
        self.assertEqual(item.capability_operation, executed.result.operation)
        self.assertEqual(item.decision_kind, "dialogue")
        unrelated = self.send(system, "コーヒーについて教えて")
        self.assertEqual(unrelated.experience_information.selections, ())
        related = self.send(system, "document.txtについて教えて")
        self.assertEqual(related.experience_information.selections[0].item, item)
        self.assertEqual(related.intelligence_request.proposal.constraints.max_output_characters, 160)
        self.send(system, "成功したと思う")
        self.assertEqual(next(e for e in system.memory.experience_evidence if e.evidence_id == evidence.evidence_id), evidence)
        system.start_new_dialogue_context(ExternalTime(NOW.value + timedelta(seconds=self.sequence + 1)))
        self.sequence += 1
        after_new = self.send(system, "document.txtについて教えて")
        self.assertEqual(after_new.experience_information.selections, ())

    def test_unobserved_capability_claim_does_not_create_experience(self) -> None:
        system = self.make_system()
        self.send(system, "document.txtの読み取りに成功しました")
        requested = self.send(system, "経験：能力:unobserved-result：長すぎた")
        self.assertNotEqual(requested.experience_change_record.status, ExperienceChangeStatus.CONFIRMATION_REQUIRED)
        self.send(system, "経験確認")
        self.assertEqual(system.memory.experience_items, ())
        self.assertTrue(all(e.kind is not ExperienceEvidenceKind.CAPABILITY_RESULT for e in system.memory.experience_evidence))

    def test_unknown_capability_result_is_not_reclassified_by_human_success_claim(self) -> None:
        system = self.make_system(
            capability_reader=UnknownResultReader(),
            capability_authority_id="human-001",
            authorized_read_targets=("document.txt",),
        )
        self.send(system, "document.txtについて相談したい")
        self.sequence += 1
        executed = system.use_read_capability(
            target="document.txt",
            authority_id="human-001",
            idempotency_key="unknown-experience-read",
            requested_at=ExternalTime(NOW.value + timedelta(seconds=self.sequence)),
        )
        self.assertEqual(executed.result.status, CapabilityResultStatus.UNKNOWN)
        self.send(system, "読み取りは成功しました")
        self.assertEqual(system.memory.experience_items, ())
        self.send(system, f"経験：能力:{executed.result.result_id}：長すぎた")
        self.send(system, "経験確認")
        evidence = next(e for e in system.memory.experience_evidence if e.kind is ExperienceEvidenceKind.CAPABILITY_RESULT)
        self.assertEqual(evidence.capability_status, CapabilityResultStatus.UNKNOWN)
        self.assertEqual(system.memory.experience_items[0].evidence_id, evidence.evidence_id)
        system.shutdown()
        restored = self.make_system(require_existing_persistence=True)
        self.assertEqual(next(e for e in restored.memory.experience_evidence if e.evidence_id == evidence.evidence_id), evidence)

    def test_capability_experience_does_not_match_other_path_with_same_basename(self) -> None:
        system, item = self._adopt_capability_experience()
        self.assertEqual(item.capability_target, "document.txt")
        for text in ("other/document.txtについて", "document.txt.bakについて", "mydocument.txtについて"):
            with self.subTest(text=text):
                self.assertEqual(self.send(system, text).experience_information.selections, ())
        self.assertEqual(self.send(system, "document.txtについて").experience_information.selections[0].item, item)

    def test_pending_cannot_be_confirmed_after_context_switch_but_can_be_cancelled(self) -> None:
        system = self.make_system()
        self.send(system, "こんにちは")
        self.send(system, "経験：直前：長すぎた")
        system.start_new_dialogue_context(ExternalTime(NOW.value + timedelta(seconds=self.sequence + 1)))
        self.sequence += 1
        self.send(system, "経験確認")
        self.assertEqual(system.memory.experience_items, ())
        self.send(system, "経験取消")
        self.assertEqual(system.memory.experience_pending_confirmations, ())
        self.adopt(system)
        self.assertEqual(len(system.memory.experience_items), 1)

    def test_experience_can_be_rejected_from_new_context_by_same_counterpart(self) -> None:
        system = self.make_system()
        original = self.adopt(system, scope=" | 話題=コーヒー")
        system.start_new_dialogue_context(ExternalTime(NOW.value + timedelta(seconds=self.sequence + 1)))
        self.sequence += 1
        proposed = self.send(system, f"経験不採用：{original.item_id}")
        self.assertEqual(proposed.experience_change_record.status, ExperienceChangeStatus.CONFIRMATION_REQUIRED)
        confirmed = self.send(system, "経験確認")
        revised = confirmed.experience_change_record.after
        self.assertFalse(revised.active)
        self.assertEqual(revised.context_id, original.context_id)
        self.assertEqual(revised.evidence_id, original.evidence_id)
        self.assertEqual(revised.version, 2)
        self.assertEqual(self.send(system, "コーヒーについて教えて").experience_information.selections, ())

    def test_new_feedback_replaces_expired_pending_without_blocking_progress(self) -> None:
        system = self.make_system()
        self.send(system, "こんにちは")
        self.send(system, "経験：直前：長すぎた")
        old_pending = system.memory.experience_pending_confirmations[0]
        self.sequence = 86402
        self.send(system, "コーヒーについてもう一度")
        proposed = self.send(system, "経験：直前：説明が足りなかった")
        self.assertEqual(proposed.experience_change_record.status, ExperienceChangeStatus.CONFIRMATION_REQUIRED)
        new_pending = system.memory.experience_pending_confirmations[0]
        self.assertNotEqual(new_pending.pending_id, old_pending.pending_id)
        item = self.send(system, "経験確認").experience_change_record.after
        self.assertEqual(item.value, ExperienceValue.DETAILED)
        self.assertEqual(system.memory.experience_pending_confirmations, ())


if __name__ == "__main__":
    unittest.main()
