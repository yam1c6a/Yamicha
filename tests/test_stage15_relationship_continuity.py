from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import patch


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from yamicha.adapters.intelligence import OllamaChatAdapter  # noqa: E402
from yamicha.body.persistence import PersistenceCommitError  # noqa: E402
from yamicha.bootstrap import (  # noqa: E402
    InteractiveConsole,
    make_stage14_system,
    make_stage15_system,
)
from yamicha.contracts import (  # noqa: E402
    CapabilityDispatchStatus,
    ClockObservation,
    ConversationMemoryOperationStatus,
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
    SourceVerification,
)
from yamicha.contracts.relationship import (  # noqa: E402
    RelationshipChangeStatus,
    RelationshipInformationKind,
    RelationshipInformationStatus,
)


NOW = ExternalTime(datetime(2026, 10, 27, 12, 0, tzinfo=UTC))


class FixedClock:
    def __init__(self, at: ExternalTime = NOW) -> None:
        self.at = at

    def observe(self) -> ClockObservation:
        return ClockObservation(external=self.at, monotonic=MonotonicTime(100.0))


class FakeTransport:
    def __init__(self, *, unavailable: bool = False, reply: str = "応答") -> None:
        self.requests = []
        self.unavailable = unavailable
        self.reply = reply

    def generate(self, request):
        self.requests.append(request)
        return ExternalIntelligenceResponse(
            status=(
                IntelligenceResultStatus.UNAVAILABLE
                if self.unavailable
                else IntelligenceResultStatus.SUCCESS
            ),
            model=request.proposal.model,
            content=(None if self.unavailable else json.dumps({"reply": self.reply})),
            detail="stage-15 deterministic test transport",
        )


class FakeHTTPResponse:
    def __init__(self, model: str) -> None:
        self.model = model

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(
            {"model": self.model, "done": True, "message": {"content": '{"reply":"応答"}'}}
        ).encode("utf-8")


def raw(input_id: str, text: str, seconds: int = 0, *, source_id: str = "human-001"):
    return RawTextInput(
        input_id=input_id,
        received_at=ExternalTime(NOW.value + timedelta(seconds=seconds)),
        source_id=source_id,
        content=text,
        source_verification=SourceVerification.VERIFIED,
    )


class Stage15RelationshipContinuityTest(unittest.TestCase):
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

    def make_system(self, transport=None, **options):
        system = make_stage15_system(
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
        return system.receive_text(raw(f"input-{self.sequence:03}", text, self.sequence, **options))

    def confirm_item(self, system, text: str):
        self.send(system, text)
        self.send(system, "関係確認")
        return system.relationship.relationship_items[-1]

    def test_addition_requires_separate_confirmation_and_is_not_memory(self) -> None:
        transport = FakeTransport()
        system = self.make_system(transport)
        requested = self.send(system, "関係：希望：説明は短めに")

        self.assertEqual(requested.relationship_change_record.status, RelationshipChangeStatus.CONFIRMATION_REQUIRED)
        self.assertEqual(system.relationship.relationship_items, ())
        self.assertEqual(len(system.relationship.relationship_pending_confirmations), 1)
        self.assertEqual(system.memory.conversation_memory_items, ())
        self.assertEqual(transport.requests, [])
        self.send(system, "はい")
        self.assertEqual(system.relationship.relationship_items, ())

        confirmed = self.send(system, "関係確認")

        self.assertEqual(confirmed.relationship_change_record.status, RelationshipChangeStatus.COMPLETED)
        item = system.relationship.relationship_items[0]
        self.assertEqual(item.content, "説明は短めに")
        self.assertEqual(item.kind, RelationshipInformationKind.PREFERENCE)
        self.assertEqual(item.version, 1)
        self.assertNotIn(item.confirmation_reference, item.source_references)
        self.assertEqual(system.relationship.relationship_pending_confirmations, ())
        self.assertEqual(system.memory.conversation_memory_items, ())
        outcome = self.send(system, "こんにちは")
        self.assertEqual(outcome.context.relationship.relationship_information.selections[0].item, item)
        self.assertEqual(outcome.intelligence_request.proposal.relationship_information.selections[0].item, item)

    def test_all_kinds_remain_unconfirmed_until_another_input(self) -> None:
        system = self.make_system()
        cases = (
            ("希望", RelationshipInformationKind.PREFERENCE),
            ("合意", RelationshipInformationKind.AGREEMENT),
            ("配慮", RelationshipInformationKind.CONSIDERATION),
            ("継続事項", RelationshipInformationKind.ONGOING),
            ("距離", RelationshipInformationKind.DISTANCE),
        )
        for index, (label, kind) in enumerate(cases):
            self.send(system, f"関係：{label}：確認対象{index}")
            self.assertEqual(len(system.relationship.relationship_items), index)
            self.send(system, "関係確認")
            self.assertEqual(system.relationship.relationship_items[-1].kind, kind)

    def test_pending_confirmation_and_history_survive_restart(self) -> None:
        first = self.make_system()
        self.send(first, "関係：合意：作業前に内容を相談する")
        snapshot = first.relationship.persistence_snapshot()
        identity = first.recovery.identity.subject_id
        first.shutdown()

        restored = self.make_system(require_existing_persistence=True)

        self.assertEqual(restored.recovery.identity.subject_id, identity)
        self.assertEqual(restored.relationship.persistence_snapshot(), snapshot)
        self.send(restored, "関係確認")
        item = restored.relationship.relationship_items[0]
        self.assertEqual(item.content, "作業前に内容を相談する")
        self.assertEqual(len(restored.relationship.relationship_changes), 2)

    def test_correction_keeps_before_after_and_only_uses_confirmed_new_version(self) -> None:
        transport = FakeTransport()
        system = self.make_system(transport)
        original = self.confirm_item(system, "関係：希望：説明は短めに")
        correction = self.send(system, f"関係訂正：{original.item_id}：説明は詳しく")
        self.assertIsNone(correction.intelligence_request)
        self.assertEqual(system.relationship.relationship_items[0], original)

        confirmed = self.send(system, "関係確認")
        record = confirmed.relationship_change_record
        self.assertEqual(record.before, original)
        self.assertEqual(record.after.item_id, original.item_id)
        self.assertEqual(record.after.version, 2)
        self.assertEqual(record.after.content, "説明は詳しく")
        self.assertTrue(record.reason)
        self.assertTrue(record.impact_scope)
        next_turn = self.send(system, "こんにちは")
        proposal = next_turn.intelligence_request.proposal
        self.assertEqual(tuple(s.item.content for s in proposal.relationship_information.selections), ("説明は詳しく",))
        self.assertTrue(all("説明は短めに" not in turn.text for turn in proposal.dialogue_context.turns))

    def test_withdrawal_excludes_item_from_next_judgment_and_llm(self) -> None:
        system = self.make_system()
        item = self.confirm_item(system, "関係：距離：丁寧な呼び方を希望する")
        withdrawn = self.send(system, f"関係撤回：{item.item_id}")
        self.assertEqual(withdrawn.relationship_change_record.status, RelationshipChangeStatus.COMPLETED)
        self.assertIsNone(withdrawn.intelligence_request)
        self.assertEqual(system.relationship.relationship_items[0].status, RelationshipInformationStatus.WITHDRAWN)

        next_turn = self.send(system, "こんにちは")

        self.assertEqual(next_turn.context.relationship.relationship_information.selections, ())
        self.assertEqual(next_turn.intelligence_request.proposal.relationship_information.selections, ())
        self.assertTrue(all(item.content not in turn.text for turn in next_turn.intelligence_request.proposal.dialogue_context.turns))

    def test_cancel_does_not_create_information_and_unknown_id_is_not_found(self) -> None:
        system = self.make_system()
        self.send(system, "関係：配慮：急かす言い方を避ける")
        cancelled = self.send(system, "関係取消")
        self.assertEqual(cancelled.relationship_change_record.status, RelationshipChangeStatus.CANCELLED)
        self.assertEqual(system.relationship.relationship_pending_confirmations, ())
        self.assertEqual(system.relationship.relationship_items, ())
        absent = self.send(system, "関係撤回：存在しないID")
        self.assertEqual(absent.relationship_change_record.status, RelationshipChangeStatus.NOT_FOUND)

    def test_withdrawal_also_excludes_old_relation_dependent_dialogue(self) -> None:
        transport = FakeTransport(reply="説明は短めにしておきます。")
        system = self.make_system(transport)
        item = self.confirm_item(system, "関係：希望：説明は短めに")
        self.send(system, "こんにちは")
        self.send(system, f"関係撤回：{item.item_id}")

        next_turn = self.send(system, "次の話題")

        self.assertTrue(
            all("説明は短めに" not in turn.text for turn in next_turn.intelligence_request.proposal.dialogue_context.turns)
        )

    def test_withdrawal_prevents_later_confirmation_of_stale_correction(self) -> None:
        system = self.make_system()
        item = self.confirm_item(system, "関係：希望：説明は短めに")
        self.send(system, f"関係訂正：{item.item_id}：説明は詳しく")
        self.send(system, f"関係撤回：{item.item_id}")
        result = self.send(system, "関係確認")

        self.assertIn(result.relationship_change_record.status, (
            RelationshipChangeStatus.STALE,
            RelationshipChangeStatus.NOT_FOUND,
            RelationshipChangeStatus.REJECTED,
        ))
        self.assertEqual(system.relationship.relationship_items[0].status, RelationshipInformationStatus.WITHDRAWN)

    def test_relation_omitted_by_limit_cannot_propagate_through_prior_dialogue(self) -> None:
        old_content = "古い希望Aは短い説明"
        transport = FakeTransport(reply=f"{old_content}として扱います。")
        system = self.make_system(transport)
        original = self.confirm_item(system, f"関係：希望：{old_content}")
        self.send(system, "最初の話題")
        for index in range(6):
            self.confirm_item(system, f"関係：希望：新しい希望{index}")
        transport.reply = "新しい希望についての応答"

        after_limit = self.send(system, "次の話題").intelligence_request.proposal

        self.assertNotIn(original.item_id, {selection.item.item_id for selection in after_limit.relationship_information.selections})
        self.assertTrue(all(old_content not in turn.text for turn in after_limit.dialogue_context.turns))
        self.send(system, f"関係撤回：{original.item_id}")
        after_withdrawal = self.send(system, "さらに次の話題").intelligence_request.proposal
        self.assertNotIn(original.item_id, {selection.item.item_id for selection in after_withdrawal.relationship_information.selections})
        self.assertTrue(all(old_content not in turn.text for turn in after_withdrawal.dialogue_context.turns))

    def test_ordinary_or_invalid_statements_do_not_invent_relationship(self) -> None:
        system = self.make_system()
        for text in ("私たちは仲良しだね", "彼は説明が短いほうを好む", "関係：未知の分類：内容", "関係：希望："):
            self.send(system, text)
        self.assertEqual(system.relationship.relationship_items, ())
        self.assertEqual(system.relationship.relationship_pending_confirmations, ())

    def test_other_counterpart_cannot_confirm_and_duplicate_input_is_idempotent(self) -> None:
        system = self.make_system()
        original = raw("same-input", "関係：希望：説明は短めに")
        system.receive_text(original)
        before = system.relationship.persistence_snapshot()
        duplicate = system.receive_text(original)
        self.assertEqual(duplicate.disposition, InputDisposition.DUPLICATE)
        self.assertEqual(system.relationship.persistence_snapshot(), before)
        rejected = self.send(system, "関係確認", source_id="another-human")
        self.assertNotEqual(rejected.disposition, InputDisposition.ACCEPTED)
        self.assertEqual(system.relationship.relationship_items, ())
        self.assertEqual(system.relationship.relationship_pending_confirmations, before.relationship_pending_confirmations)

    def test_scope_and_expiry_limit_use_and_invalid_expiry_is_rejected(self) -> None:
        system = self.make_system()
        item = self.confirm_item(
            system,
            "関係：配慮：飲み物は温かいものを | 話題=コーヒー,紅茶 | 期限=2026-10-27T12:01:00+00:00",
        )
        unrelated = self.send(system, "天気について")
        self.assertEqual(unrelated.intelligence_request.proposal.relationship_information.selections, ())
        related = self.send(system, "紅茶について")
        self.assertEqual(related.intelligence_request.proposal.relationship_information.selections[0].item, item)
        expired = system.receive_text(raw("expired", "紅茶について", 61))
        self.assertEqual(expired.intelligence_request.proposal.relationship_information.selections, ())
        invalid = system.receive_text(raw("past-expiry", "関係：希望：本文 | 期限=2026-10-26T12:00:00+00:00", 62))
        self.assertEqual(invalid.relationship_change_record.status, RelationshipChangeStatus.REJECTED)

    def test_expired_pending_is_not_confirmed(self) -> None:
        system = self.make_system()
        self.send(system, "関係：合意：作業前に内容を相談する")
        outcome = system.receive_text(raw("late-confirm", "関係確認", 86402))
        self.assertEqual(outcome.relationship_change_record.status, RelationshipChangeStatus.STALE)
        self.assertEqual(system.relationship.relationship_items, ())

    def test_selection_is_bounded_and_current_input_keeps_priority(self) -> None:
        system = self.make_system()
        for index in range(7):
            self.confirm_item(system, f"関係：希望：希望{index}" + "あ" * 100)
        outcome = self.send(system, "こんにちは")
        proposal = outcome.intelligence_request.proposal
        window = proposal.relationship_information
        self.assertGreater(len(window.selections), 0)
        self.assertLessEqual(len(window.selections), 5)
        self.assertLessEqual(window.total_characters, 512)
        self.assertLessEqual(
            proposal.dialogue_context.total_characters
            + proposal.conversation_memory.total_characters
            + window.total_characters,
            proposal.constraints.max_input_characters,
        )
        long_text = "現在の質問" + "あ" * 4080
        near_limit = self.send(system, long_text).intelligence_request.proposal
        self.assertEqual(near_limit.input_text, long_text)
        self.assertEqual(near_limit.relationship_information.selections, ())
        self.assertLessEqual(
            near_limit.dialogue_context.total_characters
            + near_limit.conversation_memory.total_characters,
            near_limit.constraints.max_input_characters,
        )

    def test_new_context_and_model_change_preserve_information(self) -> None:
        first = self.make_system()
        item = self.confirm_item(first, "関係：希望：説明は短めに")
        first.start_new_dialogue_context(ExternalTime(NOW.value + timedelta(seconds=3)))
        self.sequence = 3
        after_new = self.send(first, "こんにちは")
        self.assertEqual(after_new.intelligence_request.proposal.relationship_information.selections[0].item, item)
        changes = first.relationship.relationship_changes
        first.shutdown()

        restored = self.make_system(intelligence_model="replacement-local-model")
        after_restart = self.send(restored, "こんにちは")

        self.assertEqual(after_restart.intelligence_request.proposal.model, "replacement-local-model")
        self.assertEqual(after_restart.intelligence_request.proposal.relationship_information.selections[0].item, item)
        self.assertEqual(restored.relationship.relationship_changes, changes)

    def test_unavailable_llm_does_not_prevent_relationship_operations(self) -> None:
        transport = FakeTransport(unavailable=True)
        system = self.make_system(transport)
        item = self.confirm_item(system, "関係：希望：説明は短めに")
        self.assertEqual(transport.requests, [])
        outcome = self.send(system, "こんにちは")
        self.assertEqual(outcome.intelligence_result.status, IntelligenceResultStatus.UNAVAILABLE)
        self.assertIsNotNone(outcome.dialogue_output)
        self.assertEqual(system.relationship.relationship_items[0], item)

    def test_agreement_and_distance_never_grant_external_authority(self) -> None:
        system = self.make_system()
        self.confirm_item(system, "関係：合意：ファイル操作を毎回許可する")
        self.confirm_item(system, "関係：距離：親しい相手として話してほしい")
        outcome = self.send(system, "こんにちは")
        self.assertIsNone(outcome.context.relationship.current_consent)
        self.assertFalse(outcome.context.boundary.external_effects_permitted)
        capability = system.use_read_capability(
            target="unauthorized.txt",
            authority_id="human-001",
            idempotency_key="relationship-must-not-authorize",
            requested_at=ExternalTime(NOW.value + timedelta(seconds=10)),
        )
        self.assertEqual(capability.dispatch_status, CapabilityDispatchStatus.REJECTED)

    def test_failed_checkpoint_recovers_last_confirmed_information(self) -> None:
        system = self.make_system()
        original = self.confirm_item(system, "関係：希望：説明は短めに")
        self.send(system, f"関係訂正：{original.item_id}：説明は詳しく")
        committed = system.relationship.persistence_snapshot()
        system.persistence._connection.execute(
            "CREATE TRIGGER reject_stage15_checkpoint BEFORE INSERT ON checkpoints "
            "BEGIN SELECT RAISE(ABORT, 'simulated interruption'); END"
        )

        with self.assertRaises(PersistenceCommitError):
            self.send(system, "関係確認")
        with self.assertRaises(RuntimeError):
            self.send(system, "こんにちは")
        system.abandon()
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("DROP TRIGGER reject_stage15_checkpoint")
            connection.commit()
        finally:
            connection.close()
        restored = self.make_system(require_existing_persistence=True)

        self.assertEqual(restored.relationship.persistence_snapshot(), committed)
        self.assertEqual(restored.relationship.relationship_items[0], original)
        self.send(restored, "関係確認")
        self.assertEqual(restored.relationship.relationship_items[0].content, "説明は詳しく")

    def test_stage14_database_upgrades_without_inventing_relationship_information(self) -> None:
        stage14 = make_stage14_system(
            persistence_path=self.path,
            clock=FixedClock(),
            persistence_time_factory=lambda: NOW,
            intelligence_transport=FakeTransport(),
            subject_id_factory=lambda: "existing-life",
        )
        self.systems.append(stage14)
        self.send(stage14, "覚えて：猫が好き")
        stage14.shutdown()
        stage15 = self.make_system(require_existing_persistence=True)

        self.assertEqual(stage15.recovery.identity.subject_id, "existing-life")
        self.assertEqual(stage15.recovery.identity.configuration_version, "stage15-v1")
        self.assertEqual(stage15.memory.conversation_memory_items[0].meaning, "猫が好き")
        self.assertEqual(stage15.relationship.relationship_items, ())
        self.assertEqual(stage15.relationship.relationship_changes, ())

    def test_adapter_labels_relationship_content_and_scope_checks_are_enforced(self) -> None:
        system = self.make_system()
        item = self.confirm_item(system, "関係：希望：説明は短めに")
        outcome = self.send(system, "こんにちは")
        request = outcome.intelligence_request
        captured = {}

        def open_request(http_request, timeout):
            captured.update(json.loads(http_request.data.decode("utf-8")))
            return FakeHTTPResponse(request.proposal.model)

        with patch("urllib.request.urlopen", side_effect=open_request):
            result = OllamaChatAdapter().generate(request)

        self.assertEqual(result.status, IntelligenceResultStatus.SUCCESS)
        messages = captured["messages"]
        relation_messages = [message for message in messages if item.content in message["content"]]
        self.assertEqual(len(relation_messages), 1)
        self.assertIn(item.item_id, relation_messages[0]["content"])
        self.assertIn("権限", relation_messages[0]["content"])
        with self.assertRaises(ValueError):
            replace(
                request.proposal,
                constraints=replace(request.proposal.constraints, max_input_characters=1),
            )
        with self.assertRaises(ValueError):
            replace(
                request.proposal,
                constraints=replace(
                    request.proposal.constraints,
                    allowed_input_scope=tuple(
                        scope for scope in request.proposal.constraints.allowed_input_scope
                        if scope != "selected_confirmed_relationship_information"
                    ),
                ),
            )

    def test_console_shows_confirmation_and_identified_relationship_list(self) -> None:
        system = self.make_system()
        output = StringIO()
        inputs = iter(("console-1", "console-2"))
        times = iter(ExternalTime(NOW.value + timedelta(seconds=n)) for n in range(10))
        console = InteractiveConsole(
            input_stream=StringIO(
                "関係：希望：説明は短めに\n/relationship\n関係確認\n/relationship\n/quit\n"
            ),
            output_stream=output,
            system=system,
            input_id_factory=inputs.__next__,
            external_time_factory=times.__next__,
            source_id="human-001",
        )

        self.assertEqual(console.run(), 0)

        shown = output.getvalue()
        self.assertIn("Yamicha 対話コンソール（段階15）", shown)
        self.assertIn("関係情報の確認待ち", shown)
        self.assertIn("関係取消", shown)
        self.assertIn("確認した関係情報を保存しました。", shown)
        self.assertIn(system.relationship.relationship_items[0].item_id, shown)
        self.assertIn("説明は短めに", shown)
        self.assertEqual(system.sensation.reception_count, 2)

    def test_boundary_refusal_does_not_apply_relationship_change_or_break_next_input(self) -> None:
        system = self.make_system()

        refused = self.send(system, "関係：合意：許可なく秘密を公開する")

        self.assertEqual(refused.finalization.finalized_direction, DecisionDirection.REFUSE)
        self.assertEqual(system.relationship.relationship_items, ())
        self.assertEqual(system.relationship.relationship_pending_confirmations, ())
        self.assertIsNone(refused.intelligence_request)
        next_turn = self.send(system, "こんにちは")
        self.assertEqual(next_turn.disposition, InputDisposition.ACCEPTED)

    def _check_information_lists_blocked(self, system) -> None:
        output = StringIO()
        received_before = system.sensation.reception_count
        checkpoint_before = system.persistence.latest_sequence
        console = InteractiveConsole(
            input_stream=StringIO(
                "/relationship\n/relationship all\n/memory\n/memory all\n/quit\n"
            ),
            output_stream=output,
            system=system,
            external_time_factory=lambda: ExternalTime(NOW.value + timedelta(seconds=60)),
            source_id="human-001",
        )
        # Keep the store available to inspect whether the read-only commands
        # changed ordinary checkpoints or recorded their denial in the audit.
        with patch.object(type(system), "shutdown"):
            self.assertEqual(console.run(), 0)
        shown = output.getvalue()
        for private_text in ("一覧確認用の希望", "一覧確認用の撤回事項", "一覧確認用の確認待ち", "一覧確認用の記憶"):
            self.assertNotIn(private_text, shown)
        self.assertEqual(system.sensation.reception_count, received_before)
        self.assertEqual(system.persistence.latest_sequence, checkpoint_before)
        audit = tuple(
            record for record in system.persistence.protection_audit_records()
            if record.target == "owned-information-display"
        )
        self.assertEqual(len(audit), 4)
        self.assertTrue(all(
            record.kind is ProtectionAuditKind.PERMISSION
            and record.decision is ProtectionDecision.REJECTED
            for record in audit
        ))

    def _seed_information_lists(self, **options):
        system = self.make_system(**options)
        self.send(system, "覚えて：一覧確認用の記憶")
        self.confirm_item(system, "関係：希望：一覧確認用の希望")
        withdrawn = self.confirm_item(system, "関係：配慮：一覧確認用の撤回事項")
        self.send(system, f"関係撤回：{withdrawn.item_id}")
        self.send(system, "関係：距離：一覧確認用の確認待ち")
        return system

    def test_protection_blocks_active_withdrawn_and_pending_information_lists(self) -> None:
        system = self._seed_information_lists()
        system.activate_fixed_protection(ExternalTime(NOW.value + timedelta(seconds=30)))

        self._check_information_lists_blocked(system)

        self.assertEqual(system.protection_boundary.mode, ProtectionMode.PROTECTED)

    def test_restored_protection_still_blocks_information_lists(self) -> None:
        system = self._seed_information_lists()
        before = system.relationship.persistence_snapshot()
        system.activate_fixed_protection(ExternalTime(NOW.value + timedelta(seconds=30)))
        system.shutdown()
        restored = self.make_system(require_existing_persistence=True)

        self._check_information_lists_blocked(restored)

        self.assertEqual(restored.protection_boundary.mode, ProtectionMode.PROTECTED)
        self.assertEqual(restored.relationship.persistence_snapshot(), before)

    def test_disabled_normal_output_blocks_information_lists(self) -> None:
        system = self._seed_information_lists(normal_dialogue_output_enabled=False)

        self._check_information_lists_blocked(system)

        self.assertEqual(system.protection_boundary.mode, ProtectionMode.NORMAL)

    def test_disabled_normal_output_blocks_management_result_content(self) -> None:
        system = self.make_system(normal_dialogue_output_enabled=False)
        output = StringIO()
        times = iter(ExternalTime(NOW.value + timedelta(seconds=n)) for n in range(10))
        console = InteractiveConsole(
            input_stream=StringIO(
                "関係：希望：結果確認用の非公開事項\n関係確認\n"
                "覚えて：私はコーヒーが好き\nコーヒーじゃなくて紅茶\n/quit\n"
            ),
            output_stream=output,
            system=system,
            external_time_factory=times.__next__,
            source_id="human-001",
        )

        self.assertEqual(console.run(), 0)

        shown = output.getvalue()
        self.assertNotIn("結果確認用の非公開事項", shown)
        self.assertNotIn("確認した関係情報を保存しました", shown)
        self.assertNotIn("記憶を訂正しました", shown)
        self.assertEqual(system.relationship.relationship_items[0].content, "結果確認用の非公開事項")
        self.assertEqual(system.memory.conversation_memory_items[0].meaning, "私は紅茶が好き")

    def test_physical_memory_deletion_still_completes_in_stage15(self) -> None:
        system = self.make_system()
        self.send(system, "覚えて：猫が好き")
        requested = self.send(system, "猫を完全に削除して")
        self.assertEqual(
            requested.conversation_memory_governance_record.status,
            ConversationMemoryOperationStatus.CONFIRMATION_REQUIRED,
        )

        completed = self.send(system, "はい")

        self.assertEqual(
            completed.conversation_memory_governance_record.status,
            ConversationMemoryOperationStatus.COMPLETED,
        )
        self.assertTrue(system.memory.conversation_memory_items[0].physically_deleted)
        self.assertEqual(system.relationship.relationship_items, ())

    def test_relationship_and_physical_deletion_confirmations_do_not_cross(self) -> None:
        system = self.make_system()
        self.send(system, "覚えて：猫が好き")
        self.send(system, "関係：希望：説明は短めに")
        relationship_pending = system.relationship.relationship_pending_confirmations
        self.send(system, "猫を完全に削除して")
        self.assertEqual(len(system.memory.conversation_memory_pending_confirmations), 1)

        deleted = self.send(system, "はい")

        self.assertEqual(
            deleted.conversation_memory_governance_record.status,
            ConversationMemoryOperationStatus.COMPLETED,
        )
        self.assertEqual(system.relationship.relationship_items, ())
        self.assertEqual(system.relationship.relationship_pending_confirmations, relationship_pending)
        confirmed = self.send(system, "関係確認")
        self.assertEqual(confirmed.relationship_change_record.status, RelationshipChangeStatus.COMPLETED)
        self.assertIsNone(confirmed.conversation_memory_governance_record)
        self.assertEqual(system.relationship.relationship_items[0].content, "説明は短めに")
        self.assertTrue(system.memory.conversation_memory_items[0].physically_deleted)

    def test_relationship_confirmation_keeps_pending_physical_deletion_unchanged(self) -> None:
        system = self.make_system()
        self.send(system, "覚えて：猫が好き")
        self.send(system, "猫を完全に削除して")
        memory_pending = system.memory.conversation_memory_pending_confirmations
        self.send(system, "関係：希望：説明は短めに")

        confirmed = self.send(system, "関係確認")

        self.assertEqual(confirmed.relationship_change_record.status, RelationshipChangeStatus.COMPLETED)
        self.assertIsNone(confirmed.conversation_memory_governance_record)
        self.assertEqual(system.memory.conversation_memory_pending_confirmations, memory_pending)
        self.assertFalse(system.memory.conversation_memory_items[0].physically_deleted)
        deleted = self.send(system, "はい")
        self.assertEqual(
            deleted.conversation_memory_governance_record.status,
            ConversationMemoryOperationStatus.COMPLETED,
        )


if __name__ == "__main__":
    unittest.main()
