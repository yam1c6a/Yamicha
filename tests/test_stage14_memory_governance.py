from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from yamicha.bootstrap import (  # noqa: E402
    InteractiveConsole,
    make_stage13_system,
    make_stage14_system,
)
from yamicha.contracts import (  # noqa: E402
    ClockObservation,
    ConversationMemoryOperationKind,
    ConversationMemoryOperationStatus,
    ExternalIntelligenceResponse,
    ExternalTime,
    InformationCertainty,
    IntelligenceResultStatus,
    MonotonicTime,
    RawTextInput,
    SourceVerification,
)
from yamicha.life.stage14.memory import REDACTED_MEMORY_MEANING  # noqa: E402


NOW = ExternalTime(datetime(2026, 8, 16, 12, 0, tzinfo=UTC))


class FixedClock:
    def __init__(self, at: ExternalTime = NOW) -> None:
        self.at = at

    def observe(self) -> ClockObservation:
        return ClockObservation(external=self.at, monotonic=MonotonicTime(100.0))


class FakeTransport:
    def __init__(self, replies: tuple[str, ...] = ("応答",)) -> None:
        self.replies = replies
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        reply = self.replies[min(len(self.requests) - 1, len(self.replies) - 1)]
        return ExternalIntelligenceResponse(
            status=IntelligenceResultStatus.SUCCESS,
            model=request.proposal.model,
            content=json.dumps({"reply": reply}, ensure_ascii=False),
            detail="stage-14 fake success",
        )


def raw(input_id: str, text: str, seconds: int = 0) -> RawTextInput:
    return RawTextInput(
        input_id=input_id,
        received_at=ExternalTime(NOW.value + timedelta(seconds=seconds)),
        source_id="human-001",
        content=text,
        source_verification=SourceVerification.VERIFIED,
    )


def make_system(path: Path, **options: object):
    return make_stage14_system(
        persistence_path=path,
        clock=FixedClock(),
        persistence_time_factory=lambda: NOW,
        intelligence_transport=FakeTransport(),
        **options,
    )


class Stage14MemoryGovernanceTest(unittest.TestCase):
    def test_correction_tracks_before_after_version_and_reuses_only_new_meaning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            system = make_system(Path(directory) / "yamicha.sqlite3")
            system.receive_text(raw("input-001", "私はコーヒーが好き", 0))

            corrected = system.receive_text(
                raw("input-002", "コーヒーじゃなくて紅茶", 1)
            )

            record = corrected.conversation_memory_governance_record
            self.assertIsNotNone(record)
            assert record is not None
            self.assertEqual(record.operation, ConversationMemoryOperationKind.CORRECTION)
            self.assertEqual(record.status, ConversationMemoryOperationStatus.COMPLETED)
            self.assertEqual(record.previous_meaning, "私はコーヒーが好き")
            self.assertEqual(record.replacement_meaning, "私は紅茶が好き")
            self.assertEqual(record.affected_items[0][1:], (1, 2))
            self.assertEqual(
                system.memory.conversation_memory_items[0].meaning,
                "私は紅茶が好き",
            )
            system.start_new_dialogue_context(
                ExternalTime(NOW.value + timedelta(seconds=2))
            )
            recalled = system.receive_text(
                raw("input-003", "紅茶について何を覚えてる？", 3)
            )
            self.assertEqual(
                tuple(
                    selection.meaning
                    for selection in recalled.conversation_memory_window.selections
                ),
                ("私は紅茶が好き",),
            )
            system.shutdown()

    def test_ambiguous_correction_is_not_applied(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            system = make_system(Path(directory) / "yamicha.sqlite3")
            system.receive_text(raw("input-001", "覚えて：コーヒーが好き", 0))
            system.receive_text(raw("input-002", "覚えて：コーヒーは苦手", 1))

            outcome = system.receive_text(
                raw("input-003", "コーヒーじゃなくて紅茶", 2)
            )

            self.assertEqual(
                outcome.conversation_memory_governance_record.status,
                ConversationMemoryOperationStatus.CONFIRMATION_REQUIRED,
            )
            self.assertEqual(
                {item.meaning for item in system.memory.conversation_memory_items},
                {"コーヒーが好き", "コーヒーは苦手"},
            )
            system.shutdown()

    def test_physical_deletion_requires_confirmation_and_survives_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "yamicha.sqlite3"
            first = make_system(path)
            first.receive_text(raw("input-001", "私はコーヒーが好き", 0))
            requested = first.receive_text(
                raw("input-002", "コーヒーを完全に削除して", 1)
            )
            self.assertEqual(
                requested.conversation_memory_governance_record.status,
                ConversationMemoryOperationStatus.CONFIRMATION_REQUIRED,
            )
            self.assertEqual(
                len(first.memory.conversation_memory_pending_confirmations),
                1,
            )
            first.shutdown()

            restored = make_stage14_system(
                persistence_path=path,
                require_existing_persistence=True,
                clock=FixedClock(ExternalTime(NOW.value + timedelta(seconds=2))),
                persistence_time_factory=lambda: ExternalTime(
                    NOW.value + timedelta(seconds=2)
                ),
                intelligence_transport=FakeTransport(),
            )
            completed = restored.receive_text(raw("input-003", "はい", 3))
            record = completed.conversation_memory_governance_record
            self.assertEqual(record.status, ConversationMemoryOperationStatus.COMPLETED)
            item = restored.memory.conversation_memory_items[0]
            self.assertFalse(item.active)
            self.assertTrue(item.physically_deleted)
            self.assertEqual(item.meaning, REDACTED_MEMORY_MEANING)
            self.assertEqual(len(item.content_digest), 64)
            self.assertEqual(
                restored.memory.conversation_memory_pending_confirmations,
                (),
            )
            rows = restored.persistence._connection.execute(  # noqa: SLF001
                "SELECT payload FROM checkpoints"
            ).fetchall()
            self.assertLessEqual(len(rows), 2)
            self.assertNotIn(
                "私はコーヒーが好き",
                "".join(str(row["payload"]) for row in rows),
            )
            self.assertNotIn(
                "私はコーヒーが好き".encode("utf-8"),
                path.read_bytes(),
            )
            self.assertTrue(
                all(
                    turn.text != "私はコーヒーが好き"
                    for turn in restored.relationship.active_dialogue_context.turns
                )
            )
            restored.shutdown()

            reopened = make_stage14_system(
                persistence_path=path,
                require_existing_persistence=True,
                clock=FixedClock(ExternalTime(NOW.value + timedelta(seconds=4))),
                persistence_time_factory=lambda: ExternalTime(
                    NOW.value + timedelta(seconds=4)
                ),
                intelligence_transport=FakeTransport(),
            )
            self.assertTrue(reopened.memory.conversation_memory_items[0].physically_deleted)
            self.assertEqual(
                reopened.memory.conversation_memory_governance_records[-1].status,
                ConversationMemoryOperationStatus.COMPLETED,
            )
            reopened.shutdown()

    def test_physical_deletion_can_be_cancelled_without_changing_memory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            system = make_system(Path(directory) / "yamicha.sqlite3")
            system.receive_text(raw("input-001", "覚えて：猫が好き", 0))
            system.receive_text(raw("input-002", "猫を完全に削除して", 1))

            cancelled = system.receive_text(raw("input-003", "いいえ", 2))

            self.assertEqual(
                cancelled.conversation_memory_governance_record.status,
                ConversationMemoryOperationStatus.CANCELLED,
            )
            self.assertTrue(system.memory.conversation_memory_items[0].active)
            self.assertFalse(
                system.memory.conversation_memory_items[0].physically_deleted
            )
            system.shutdown()

    def test_physical_deletion_of_logically_forgotten_memory_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            system = make_system(Path(directory) / "yamicha.sqlite3")
            system.receive_text(raw("input-001", "覚えて：猫が好き", 0))
            system.receive_text(raw("input-002", "猫を忘れて", 1))

            requested = system.receive_text(
                raw("input-003", "猫を完全に削除して", 2)
            )
            completed = system.receive_text(raw("input-004", "はい", 3))

            self.assertEqual(
                requested.conversation_memory_governance_record.status,
                ConversationMemoryOperationStatus.CONFIRMATION_REQUIRED,
            )
            self.assertEqual(
                completed.conversation_memory_governance_record.status,
                ConversationMemoryOperationStatus.COMPLETED,
            )
            self.assertTrue(system.memory.conversation_memory_items[0].physically_deleted)
            system.shutdown()

    def test_physical_deletion_redacts_correction_history_and_candidate_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "yamicha.sqlite3"
            system = make_system(path)
            system.receive_text(raw("input-001", "私はコーヒーが好き", 0))
            system.receive_text(
                raw("input-002", "コーヒーじゃなくて紅茶", 1)
            )
            system.receive_text(raw("input-003", "紅茶を完全に削除して", 2))

            system.receive_text(raw("input-004", "はい", 3))

            correction = next(
                record
                for record in system.memory.conversation_memory_governance_records
                if record.operation is ConversationMemoryOperationKind.CORRECTION
            )
            self.assertEqual(correction.previous_meaning, REDACTED_MEMORY_MEANING)
            self.assertEqual(correction.replacement_meaning, REDACTED_MEMORY_MEANING)
            self.assertTrue(
                all(
                    candidate.meaning == REDACTED_MEMORY_MEANING
                    for candidate in system.memory.persistence_snapshot().conversation_candidates
                )
            )
            database = path.read_bytes()
            self.assertNotIn("私はコーヒーが好き".encode("utf-8"), database)
            self.assertNotIn("私は紅茶が好き".encode("utf-8"), database)
            self.assertEqual(len(system.core.lifecycle_records), 4)
            system.shutdown()

    def test_governance_can_delete_memory_larger_than_llm_context_share(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            system = make_system(Path(directory) / "yamicha.sqlite3")
            long_meaning = "長い記憶" + ("あ" * 1100)
            system.receive_text(
                raw("input-001", f"覚えて：{long_meaning}", 0)
            )

            requested = system.receive_text(
                raw("input-002", "長い記憶を完全に削除して", 1)
            )
            completed = system.receive_text(raw("input-003", "はい", 2))

            self.assertEqual(
                requested.conversation_memory_window.selections,
                (),
            )
            self.assertEqual(
                requested.conversation_memory_governance_record.status,
                ConversationMemoryOperationStatus.CONFIRMATION_REQUIRED,
            )
            self.assertEqual(
                completed.conversation_memory_governance_record.status,
                ConversationMemoryOperationStatus.COMPLETED,
            )
            system.shutdown()

    def test_logical_forget_has_distinct_governance_record_and_is_not_recalled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            system = make_system(Path(directory) / "yamicha.sqlite3")
            system.receive_text(raw("input-001", "覚えて：猫が好き", 0))

            forgotten = system.receive_text(raw("input-002", "猫を忘れて", 1))

            self.assertEqual(
                forgotten.conversation_memory_governance_record.operation,
                ConversationMemoryOperationKind.LOGICAL_FORGET,
            )
            self.assertFalse(
                forgotten.conversation_memory_forget_record.physical_deletion
            )
            system.start_new_dialogue_context(
                ExternalTime(NOW.value + timedelta(seconds=2))
            )
            recalled = system.receive_text(raw("input-003", "猫は？", 3))
            self.assertEqual(recalled.conversation_memory_window.selections, ())
            system.shutdown()

    def test_unmatched_logical_forget_is_recorded_as_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            system = make_system(Path(directory) / "yamicha.sqlite3")

            outcome = system.receive_text(raw("input-001", "猫を忘れて", 0))

            self.assertIsNone(outcome.conversation_memory_forget_record)
            self.assertEqual(
                outcome.conversation_memory_governance_record.status,
                ConversationMemoryOperationStatus.NOT_FOUND,
            )
            system.shutdown()

    def test_deactivation_and_reevaluation_are_distinct_operations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            system = make_system(Path(directory) / "yamicha.sqlite3")
            system.receive_text(raw("input-001", "覚えて：猫が好き", 0))
            reevaluated = system.receive_text(
                raw("input-002", "猫を確認済みにして", 1)
            )
            deactivated = system.receive_text(
                raw("input-003", "猫を無効にして", 2)
            )

            self.assertEqual(
                reevaluated.conversation_memory_governance_record.operation,
                ConversationMemoryOperationKind.REEVALUATION,
            )
            self.assertEqual(
                system.memory.conversation_memory_items[0].certainty,
                InformationCertainty.CONFIRMED,
            )
            self.assertEqual(
                deactivated.conversation_memory_governance_record.operation,
                ConversationMemoryOperationKind.DEACTIVATION,
            )
            self.assertFalse(system.memory.conversation_memory_items[0].active)
            system.shutdown()

    def test_checkpoint_generations_are_bounded_to_two(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            system = make_system(Path(directory) / "yamicha.sqlite3")
            for number in range(5):
                system.receive_text(
                    raw(f"input-{number}", f"覚えて：項目{number}", number)
                )

            count = system.persistence._connection.execute(  # noqa: SLF001
                "SELECT COUNT(*) AS count FROM checkpoints"
            ).fetchone()["count"]
            self.assertEqual(count, 2)
            system.shutdown()

    def test_new_dialogue_context_checkpoints_are_also_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            system = make_system(Path(directory) / "yamicha.sqlite3")
            system.receive_text(raw("input-001", "こんにちは", 0))
            for seconds in range(1, 6):
                system.start_new_dialogue_context(
                    ExternalTime(NOW.value + timedelta(seconds=seconds))
                )

            count = system.persistence._connection.execute(  # noqa: SLF001
                "SELECT COUNT(*) AS count FROM checkpoints"
            ).fetchone()["count"]
            self.assertEqual(count, 2)
            system.shutdown()

    def test_pruning_failure_reports_partial_and_keeps_recoverable_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "yamicha.sqlite3"
            system = make_system(path)
            system.receive_text(raw("input-001", "覚えて：猫が好き", 0))
            system.receive_text(raw("input-002", "猫を完全に削除して", 1))
            system.persistence._connection.execute(  # noqa: SLF001
                """
                CREATE TRIGGER reject_stage14_checkpoint_pruning
                BEFORE DELETE ON checkpoints
                BEGIN
                    SELECT RAISE(ABORT, 'simulated pruning interruption');
                END
                """
            )

            outcome = system.receive_text(raw("input-003", "はい", 2))

            self.assertEqual(
                outcome.conversation_memory_governance_record.status,
                ConversationMemoryOperationStatus.PARTIAL,
            )
            latest = system.persistence._read_latest_snapshot(  # noqa: SLF001
                system.recovery.identity
            )
            self.assertIsNotNone(latest)
            self.assertEqual(
                latest.memory.conversation_items[0].meaning,
                REDACTED_MEMORY_MEANING,
            )
            system.persistence._connection.execute(  # noqa: SLF001
                "DROP TRIGGER reject_stage14_checkpoint_pruning"
            )
            system.shutdown()

    def test_stage13_database_upgrades_with_same_identity_and_memory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "yamicha.sqlite3"
            stage13 = make_stage13_system(
                persistence_path=path,
                clock=FixedClock(),
                persistence_time_factory=lambda: NOW,
                intelligence_transport=FakeTransport(),
                subject_id_factory=lambda: "life-stage13",
            )
            stage13.receive_text(raw("input-001", "覚えて：猫が好き", 0))
            stage13.shutdown()

            stage14 = make_system(path)

            self.assertEqual(stage14.recovery.identity.subject_id, "life-stage13")
            self.assertEqual(
                stage14.recovery.identity.configuration_version,
                "stage14-v1",
            )
            self.assertEqual(
                stage14.memory.conversation_memory_items[0].meaning,
                "猫が好き",
            )
            stage14.shutdown()

    def test_console_lists_memory_and_supplements_correction_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            system = make_system(
                Path(directory) / "yamicha.sqlite3",
                known_counterpart_id="human-001",
            )
            input_ids = iter(("input-001", "input-002"))
            times = iter(
                (
                    NOW,
                    ExternalTime(NOW.value + timedelta(seconds=1)),
                )
            )
            output = StringIO()
            console = InteractiveConsole(
                input_stream=StringIO(
                    "私はコーヒーが好き\nコーヒーじゃなくて紅茶\n/memory\n/quit\n"
                ),
                output_stream=output,
                system=system,
                input_id_factory=lambda: next(input_ids),
                external_time_factory=lambda: next(times),
                source_id="human-001",
            )

            self.assertEqual(console.run(), 0)

            shown = output.getvalue()
            self.assertIn("Yamicha 対話コンソール（段階14）", shown)
            self.assertIn("記憶を訂正しました。", shown)
            self.assertIn("[有効/v2] 私は紅茶が好き", shown)


if __name__ == "__main__":
    unittest.main()
