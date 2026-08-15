from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from yamicha.adapters.intelligence import OllamaChatAdapter  # noqa: E402
from yamicha.body.persistence import PersistenceCommitError  # noqa: E402
from yamicha.bootstrap import make_stage12_system, make_stage13_system  # noqa: E402
from yamicha.contracts import (  # noqa: E402
    CandidateDisposition,
    ClockObservation,
    ContentTrust,
    ExternalIntelligenceResponse,
    ExternalTime,
    InformationCertainty,
    IntelligenceResultStatus,
    IntelligencePurpose,
    MonotonicTime,
    RawTextInput,
    SourceVerification,
)


NOW = ExternalTime(datetime(2026, 8, 15, 12, 0, tzinfo=UTC))


class FixedClock:
    def __init__(self, at: ExternalTime = NOW) -> None:
        self.at = at

    def observe(self) -> ClockObservation:
        return ClockObservation(external=self.at, monotonic=MonotonicTime(100.0))


class FakeTransport:
    def __init__(
        self,
        replies: tuple[str | dict[str, object], ...] = ("応答",),
    ) -> None:
        self.replies = replies
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        reply = self.replies[min(len(self.requests) - 1, len(self.replies) - 1)]
        content = reply if isinstance(reply, dict) else {"reply": reply}
        return ExternalIntelligenceResponse(
            status=IntelligenceResultStatus.SUCCESS,
            model=request.proposal.model,
            content=json.dumps(content, ensure_ascii=False),
            detail="stage-13 fake success",
        )


class FakeHTTPResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


class UnavailableTransport:
    def __init__(self) -> None:
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        return ExternalIntelligenceResponse(
            status=IntelligenceResultStatus.UNAVAILABLE,
            model=request.proposal.model,
            content=None,
            detail="stage-13 simulated unavailable service",
        )


def raw(input_id: str, text: str, seconds: int = 0) -> RawTextInput:
    return RawTextInput(
        input_id=input_id,
        received_at=ExternalTime(NOW.value + timedelta(seconds=seconds)),
        source_id="human-001",
        content=text,
        source_verification=SourceVerification.VERIFIED,
    )


def make_system(path: Path, transport: FakeTransport, **options: object):
    return make_stage13_system(
        persistence_path=path,
        clock=FixedClock(),
        persistence_time_factory=lambda: NOW,
        intelligence_transport=transport,
        **options,
    )


def interpreted_memory(
    *,
    action: str,
    subject: str = "self",
    meaning: str | None = None,
    target_text: str | None = None,
    certainty: str = "confirmed",
    needs_confirmation: bool = False,
    evidence_quote: str | None = None,
) -> dict[str, object]:
    return {
        "action": action,
        "subject": subject,
        "meaning": meaning,
        "target_text": target_text,
        "certainty": certainty,
        "needs_confirmation": needs_confirmation,
        "evidence_quote": evidence_quote,
    }


class Stage13ConversationMemoryTest(unittest.TestCase):
    def test_explicit_retention_request_is_adopted_with_untrusted_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            system = make_system(Path(directory) / "yamicha.sqlite3", FakeTransport())

            outcome = system.receive_text(
                raw("input-001", "覚えておいて：私はコーヒーが苦手です")
            )

            candidate = outcome.conversation_memory_candidate
            review = outcome.conversation_memory_review
            self.assertIsNotNone(candidate)
            self.assertIsNotNone(review)
            assert candidate is not None and review is not None
            self.assertEqual(candidate.meaning, "私はコーヒーが苦手です")
            self.assertEqual(candidate.certainty, InformationCertainty.CONFIRMED)
            self.assertEqual(candidate.content_trust, ContentTrust.UNTRUSTED)
            self.assertEqual(review.disposition, CandidateDisposition.ADOPTED)
            self.assertEqual(
                tuple(item.meaning for item in system.memory.conversation_memory_items),
                ("私はコーヒーが苦手です",),
            )
            system.shutdown()

    def test_unrelated_and_third_party_statements_are_not_automatically_remembered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            system = make_system(Path(directory) / "yamicha.sqlite3", FakeTransport())

            first = system.receive_text(raw("input-001", "今日は晴れですね"))
            second = system.receive_text(
                raw("input-002", "田中さんはコーヒーが好きです", 1)
            )

            self.assertIsNone(first.conversation_memory_candidate)
            self.assertIsNone(second.conversation_memory_candidate)
            self.assertEqual(system.memory.conversation_memory_items, ())
            system.shutdown()

    def test_first_person_preference_is_remembered_across_new_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transport = FakeTransport(("コーヒーがお好きなんですね。", "想起しました"))
            system = make_system(Path(directory) / "yamicha.sqlite3", transport)

            retained = system.receive_text(raw("input-001", "私はコーヒーが好き"))
            system.start_new_dialogue_context(
                ExternalTime(NOW.value + timedelta(seconds=1))
            )
            recalled = system.receive_text(raw("input-002", "私の好きなものは？", 2))

            self.assertEqual(
                retained.conversation_memory_review.disposition,
                CandidateDisposition.ADOPTED,
            )
            self.assertEqual(
                tuple(
                    selection.meaning
                    for selection in recalled.conversation_memory_window.selections
                ),
                ("私はコーヒーが好き",),
            )
            self.assertEqual(
                transport.requests[-1].proposal.dialogue_context.turns,
                (),
            )
            system.shutdown()

    def test_short_answer_to_immediately_preceding_preference_question_is_remembered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transport = FakeTransport(
                (
                    "どんな種類のコーヒーがお好みですか？",
                    "モカがお好きなんですね。",
                    "モカです。",
                )
            )
            system = make_system(Path(directory) / "yamicha.sqlite3", transport)

            system.receive_text(raw("input-001", "私はコーヒーが好き"))
            supplemented = system.receive_text(raw("input-002", "モカかな", 1))
            system.start_new_dialogue_context(
                ExternalTime(NOW.value + timedelta(seconds=2))
            )
            recalled = system.receive_text(
                raw("input-003", "どんなコーヒーが好き？", 3)
            )

            candidate = supplemented.conversation_memory_candidate
            self.assertIsNotNone(candidate)
            assert candidate is not None
            self.assertEqual(candidate.meaning, "好きなコーヒーの種類はモカ")
            self.assertEqual(candidate.certainty, InformationCertainty.INFERRED)
            self.assertEqual(
                supplemented.conversation_memory_review.disposition,
                CandidateDisposition.ADOPTED,
            )
            self.assertIn(
                "好きなコーヒーの種類はモカ",
                tuple(
                    selection.meaning
                    for selection in recalled.conversation_memory_window.selections
                ),
            )
            system.shutdown()

    def test_llm_interprets_natural_self_preference_only_after_rules_miss(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transport = FakeTransport(
                (
                    "モカがお好きなんですね。",
                    interpreted_memory(
                        action="remember",
                        meaning="好きなコーヒーの種類はモカ",
                        certainty="inferred",
                        evidence_quote="モカが好き",
                    ),
                    "モカです。",
                )
            )
            system = make_system(Path(directory) / "yamicha.sqlite3", transport)

            outcome = system.receive_text(
                raw("input-001", "特にモカが好きだよ")
            )

            self.assertIsNotNone(outcome.conversation_memory_interpretation)
            self.assertEqual(
                outcome.memory_interpretation_request.proposal.purpose,
                IntelligencePurpose.MEMORY_INTERPRETATION_CANDIDATE,
            )
            self.assertEqual(
                outcome.conversation_memory_candidate.meaning,
                "好きなコーヒーの種類はモカ",
            )
            self.assertEqual(
                outcome.conversation_memory_candidate.certainty,
                InformationCertainty.INFERRED,
            )
            system.start_new_dialogue_context(
                ExternalTime(NOW.value + timedelta(seconds=1))
            )
            recalled = system.receive_text(
                raw("input-002", "どんなコーヒーが好き？", 2)
            )
            self.assertIn(
                "好きなコーヒーの種類はモカ",
                tuple(
                    selection.meaning
                    for selection in recalled.conversation_memory_window.selections
                ),
            )
            system.shutdown()

    def test_llm_cannot_promote_third_party_or_ambiguous_interpretation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transport = FakeTransport(
                (
                    "そうなんですね。",
                    interpreted_memory(
                        action="remember",
                        subject="third_party",
                        meaning="田中さんは紅茶が好き",
                        evidence_quote="田中さんは紅茶が好き",
                    ),
                    "確認が必要です。",
                    interpreted_memory(
                        action="remember",
                        meaning="紅茶が好き",
                        certainty="inferred",
                        needs_confirmation=True,
                        evidence_quote="紅茶が好きかも",
                    ),
                )
            )
            system = make_system(Path(directory) / "yamicha.sqlite3", transport)

            third_party = system.receive_text(
                raw("input-001", "田中さんは紅茶が好き", 0)
            )
            ambiguous = system.receive_text(
                raw("input-002", "紅茶が好きかも、誰の話だったかな", 1)
            )

            self.assertIsNotNone(third_party.conversation_memory_interpretation)
            self.assertIsNone(third_party.conversation_memory_candidate)
            self.assertIsNotNone(ambiguous.conversation_memory_interpretation)
            self.assertIsNone(ambiguous.conversation_memory_candidate)
            self.assertEqual(system.memory.conversation_memory_items, ())
            system.shutdown()

    def test_llm_interpretation_with_invented_evidence_is_not_materialized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transport = FakeTransport(
                (
                    "そうなんですね。",
                    interpreted_memory(
                        action="remember",
                        meaning="紅茶が好き",
                        evidence_quote="コーヒーが好き",
                    ),
                )
            )
            system = make_system(Path(directory) / "yamicha.sqlite3", transport)

            outcome = system.receive_text(raw("input-001", "紅茶が好き"))

            self.assertIsNotNone(outcome.memory_interpretation_adoption)
            self.assertIsNone(outcome.conversation_memory_interpretation)
            self.assertIsNone(outcome.conversation_memory_candidate)
            self.assertEqual(system.memory.conversation_memory_items, ())
            system.shutdown()

    def test_llm_interpretation_cannot_replace_the_evidence_subject(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transport = FakeTransport(
                (
                    "そうなんですね。",
                    interpreted_memory(
                        action="remember",
                        meaning="コーヒーが好き",
                        evidence_quote="紅茶が好き",
                    ),
                )
            )
            system = make_system(Path(directory) / "yamicha.sqlite3", transport)

            outcome = system.receive_text(raw("input-001", "紅茶が好き"))

            self.assertIsNotNone(outcome.conversation_memory_interpretation)
            self.assertIsNone(outcome.conversation_memory_candidate)
            self.assertEqual(system.memory.conversation_memory_items, ())
            system.shutdown()

    def test_unavailable_dialogue_llm_is_not_called_again_for_memory_interpretation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transport = UnavailableTransport()
            system = make_system(Path(directory) / "yamicha.sqlite3", transport)

            outcome = system.receive_text(raw("input-001", "紅茶が好き"))

            self.assertEqual(len(transport.requests), 1)
            self.assertIsNone(outcome.memory_interpretation_request)
            self.assertIsNone(outcome.conversation_memory_candidate)
            self.assertIsNotNone(outcome.dialogue_output)
            system.shutdown()

    def test_llm_forgetting_uses_one_memory_selected_by_memory_not_an_llm_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transport = FakeTransport(
                (
                    "覚えました。",
                    "承知しました。",
                    interpreted_memory(
                        action="forget",
                        target_text="コーヒー",
                        evidence_quote="コーヒーの話はもう要らない",
                    ),
                )
            )
            system = make_system(Path(directory) / "yamicha.sqlite3", transport)
            system.receive_text(raw("input-001", "覚えて：コーヒーが好き", 0))

            outcome = system.receive_text(
                raw("input-002", "前に話したコーヒーの話はもう要らない", 1)
            )

            self.assertIsNotNone(outcome.conversation_memory_interpretation)
            self.assertIsNotNone(outcome.conversation_memory_forget_record)
            self.assertFalse(system.memory.conversation_memory_items[0].active)
            target_id = outcome.conversation_memory_forget_request.target_items[0][0]
            self.assertEqual(
                target_id,
                system.memory.conversation_memory_items[0].memory_item_id,
            )
            system.shutdown()

    def test_forget_pattern_accepts_omitted_particle_without_llm_interpretation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transport = FakeTransport(("覚えました。", "承知しました。"))
            system = make_system(Path(directory) / "yamicha.sqlite3", transport)
            system.receive_text(raw("input-001", "覚えて：コーヒーが好き", 0))

            outcome = system.receive_text(
                raw("input-002", "コーヒーのこと忘れて", 1)
            )

            self.assertIsNone(outcome.memory_interpretation_request)
            self.assertIsNotNone(outcome.conversation_memory_forget_record)
            self.assertFalse(system.memory.conversation_memory_items[0].active)
            system.shutdown()

    def test_bare_forget_logically_deactivates_only_latest_memory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            system = make_system(Path(directory) / "yamicha.sqlite3", FakeTransport())
            system.receive_text(raw("input-001", "覚えて：猫が好き", 0))
            system.receive_text(raw("input-002", "覚えて：コーヒーが好き", 1))

            forgotten = system.receive_text(raw("input-003", "忘れて", 2))

            request = forgotten.conversation_memory_forget_request
            record = forgotten.conversation_memory_forget_record
            self.assertIsNotNone(request)
            self.assertIsNotNone(record)
            assert request is not None and record is not None
            self.assertFalse(record.physical_deletion)
            states = {
                item.meaning: item.active
                for item in system.memory.conversation_memory_items
            }
            self.assertEqual(states, {"猫が好き": True, "コーヒーが好き": False})
            self.assertEqual(len(record.deactivated_items), 1)
            system.shutdown()

    def test_targeted_forget_deactivates_related_memory_and_persists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "yamicha.sqlite3"
            first = make_system(path, FakeTransport())
            first.receive_text(raw("input-001", "覚えて：猫が好き", 0))
            first.receive_text(raw("input-002", "覚えて：コーヒーが好き", 1))

            forgotten = first.receive_text(
                raw("input-003", "コーヒーのことを忘れて", 2)
            )

            self.assertIsNotNone(forgotten.conversation_memory_forget_record)
            first.shutdown()
            restored = make_stage13_system(
                persistence_path=path,
                require_existing_persistence=True,
                clock=FixedClock(ExternalTime(NOW.value + timedelta(seconds=3))),
                persistence_time_factory=lambda: ExternalTime(
                    NOW.value + timedelta(seconds=3)
                ),
                intelligence_transport=FakeTransport(),
            )
            states = {
                item.meaning: item.active
                for item in restored.memory.conversation_memory_items
            }
            self.assertEqual(states, {"猫が好き": True, "コーヒーが好き": False})
            self.assertEqual(len(restored.memory.conversation_memory_forget_records), 1)
            restored.start_new_dialogue_context(
                ExternalTime(NOW.value + timedelta(seconds=4))
            )
            recalled = restored.receive_text(
                raw("input-004", "コーヒーは？", 5)
            )
            recalled_meanings = tuple(
                selection.meaning
                for selection in recalled.conversation_memory_window.selections
            )
            self.assertNotIn("コーヒーが好き", recalled_meanings)
            self.assertEqual(recalled_meanings, ())
            restored.shutdown()

    def test_forget_without_matching_memory_does_not_create_false_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            system = make_system(Path(directory) / "yamicha.sqlite3", FakeTransport())

            outcome = system.receive_text(raw("input-001", "忘れて"))

            self.assertIsNone(outcome.conversation_memory_forget_request)
            self.assertIsNone(outcome.conversation_memory_forget_record)
            self.assertEqual(system.memory.conversation_memory_forget_records, ())
            system.shutdown()

    def test_failed_forget_checkpoint_restores_active_committed_memory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "yamicha.sqlite3"
            system = make_system(path, FakeTransport())
            system.receive_text(raw("input-001", "覚えて：コーヒーが好き"))
            system.persistence._connection.execute(  # noqa: SLF001
                """
                CREATE TRIGGER reject_stage13_forget_checkpoint
                BEFORE INSERT ON checkpoints
                BEGIN
                    SELECT RAISE(ABORT, 'simulated forgetting interruption');
                END
                """
            )

            with self.assertRaises(PersistenceCommitError):
                system.receive_text(raw("input-002", "忘れて", 1))
            system.abandon()
            connection = sqlite3.connect(path)
            connection.execute("DROP TRIGGER reject_stage13_forget_checkpoint")
            connection.commit()
            connection.close()

            restored = make_system(path, FakeTransport())
            self.assertTrue(restored.memory.conversation_memory_items[0].active)
            self.assertEqual(restored.memory.conversation_memory_forget_records, ())
            restored.shutdown()

    def test_related_memory_crosses_new_dialogue_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transport = FakeTransport(("保存", "想起"))
            system = make_system(Path(directory) / "yamicha.sqlite3", transport)
            system.receive_text(raw("input-001", "コーヒーが苦手だと覚えておいて"))
            system.start_new_dialogue_context(
                ExternalTime(NOW.value + timedelta(seconds=1))
            )

            outcome = system.receive_text(
                raw("input-002", "コーヒーについて何を覚えてる？", 2)
            )

            window = outcome.conversation_memory_window
            self.assertIsNotNone(window)
            assert window is not None
            self.assertEqual(
                tuple(selection.meaning for selection in window.selections),
                ("コーヒーが苦手",),
            )
            self.assertEqual(
                outcome.context.memory.conversation_memory_references,
                tuple(selection.memory_item_id for selection in window.selections),
            )
            self.assertEqual(
                transport.requests[-1].proposal.dialogue_context.turns,
                (),
            )
            system.shutdown()

    def test_unrelated_memory_is_not_selected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transport = FakeTransport()
            system = make_system(Path(directory) / "yamicha.sqlite3", transport)
            system.receive_text(raw("input-001", "コーヒーが苦手だと覚えておいて"))
            system.start_new_dialogue_context(
                ExternalTime(NOW.value + timedelta(seconds=1))
            )

            outcome = system.receive_text(raw("input-002", "今日の天気は？", 2))

            self.assertEqual(outcome.conversation_memory_window.selections, ())
            self.assertEqual(
                transport.requests[-1].proposal.conversation_memory.selections,
                (),
            )
            system.shutdown()

    def test_explicit_recall_is_limited_to_latest_five_items(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            system = make_system(Path(directory) / "yamicha.sqlite3", FakeTransport())
            for number in range(1, 7):
                system.receive_text(
                    raw(
                        f"input-{number:03}",
                        f"覚えて：記憶項目{number}",
                        number,
                    )
                )
            system.start_new_dialogue_context(
                ExternalTime(NOW.value + timedelta(seconds=7))
            )

            outcome = system.receive_text(raw("input-007", "何を覚えてる？", 8))

            meanings = tuple(
                selection.meaning
                for selection in outcome.conversation_memory_window.selections
            )
            self.assertEqual(
                meanings,
                tuple(f"記憶項目{number}" for number in range(6, 1, -1)),
            )
            system.shutdown()

    def test_memory_persists_and_is_selected_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "yamicha.sqlite3"
            first = make_system(path, FakeTransport())
            first.receive_text(raw("input-001", "合言葉は青い月だと覚えておいて"))
            expected = first.memory.conversation_memory_items
            first.shutdown()

            restored = make_stage13_system(
                persistence_path=path,
                require_existing_persistence=True,
                clock=FixedClock(ExternalTime(NOW.value + timedelta(seconds=2))),
                persistence_time_factory=lambda: ExternalTime(
                    NOW.value + timedelta(seconds=2)
                ),
                intelligence_transport=FakeTransport(),
            )
            restored.start_new_dialogue_context(
                ExternalTime(NOW.value + timedelta(seconds=3))
            )
            outcome = restored.receive_text(
                raw("input-002", "合言葉について何を覚えてる？", 4)
            )

            self.assertEqual(restored.memory.conversation_memory_items, expected)
            self.assertEqual(
                tuple(
                    selection.meaning
                    for selection in outcome.conversation_memory_window.selections
                ),
                ("合言葉は青い月",),
            )
            restored.shutdown()

    def test_stage12_database_upgrades_without_inventing_conversation_memory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "yamicha.sqlite3"
            stage12 = make_stage12_system(
                persistence_path=path,
                clock=FixedClock(),
                persistence_time_factory=lambda: NOW,
                intelligence_transport=FakeTransport(),
                subject_id_factory=lambda: "life-stage12",
            )
            stage12.receive_text(raw("input-001", "覚えて：旧入力"))
            stage12.shutdown()

            stage13 = make_system(path, FakeTransport())

            self.assertEqual(stage13.recovery.identity.subject_id, "life-stage12")
            self.assertEqual(stage13.recovery.identity.configuration_version, "stage13-v1")
            self.assertEqual(stage13.memory.conversation_memory_items, ())
            stage13.shutdown()

    def test_current_input_and_memory_stay_within_total_intelligence_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transport = FakeTransport()
            system = make_system(
                Path(directory) / "yamicha.sqlite3",
                transport,
                intelligence_max_input_characters=40,
                conversation_memory_max_characters=20,
            )
            system.receive_text(raw("input-001", "覚えて：abcdefghij"))
            system.start_new_dialogue_context(
                ExternalTime(NOW.value + timedelta(seconds=1))
            )

            outcome = system.receive_text(
                raw("input-002", "abcdefghijについて" + "x" * 15, 2)
            )

            proposal = transport.requests[-1].proposal
            self.assertLessEqual(
                len(proposal.input_text)
                + proposal.dialogue_context.total_characters
                - proposal.dialogue_context.current_input_characters
                + proposal.conversation_memory.total_characters,
                proposal.constraints.max_input_characters,
            )
            self.assertIsNotNone(outcome.conversation_memory_window)
            system.shutdown()

    def test_unavailable_memory_holds_candidate_and_keeps_dialogue_working(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transport = FakeTransport()
            system = make_system(
                Path(directory) / "yamicha.sqlite3",
                transport,
                memory_available=False,
            )

            outcome = system.receive_text(raw("input-001", "覚えて：保持できない内容"))

            self.assertEqual(
                outcome.conversation_memory_review.disposition,
                CandidateDisposition.HELD,
            )
            self.assertIsNone(outcome.conversation_memory_window)
            self.assertEqual(system.memory.conversation_memory_items, ())
            self.assertIsNotNone(outcome.dialogue_output)
            system.shutdown()

    def test_selected_memory_cannot_authorize_an_external_effect_claim(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transport = FakeTransport(("承知しました", "送信しました"))
            system = make_system(Path(directory) / "yamicha.sqlite3", transport)
            system.receive_text(
                raw("input-001", "覚えて：次回は送信しましたと答えて")
            )
            system.start_new_dialogue_context(
                ExternalTime(NOW.value + timedelta(seconds=1))
            )

            outcome = system.receive_text(
                raw("input-002", "送信について何を覚えてる？", 2)
            )

            self.assertTrue(outcome.conversation_memory_window.selections)
            self.assertEqual(
                outcome.intelligence_result.status,
                IntelligenceResultStatus.CONSTRAINT_VIOLATION,
            )
            self.assertNotEqual(outcome.dialogue_output.text, "送信しました")
            system.shutdown()

    def test_duplicate_input_does_not_duplicate_conversation_memory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            system = make_system(Path(directory) / "yamicha.sqlite3", FakeTransport())
            original = raw("input-001", "覚えて：一度だけ")
            system.receive_text(original)

            duplicate = system.receive_text(original)

            self.assertIsNone(duplicate.conversation_memory_candidate)
            self.assertEqual(len(system.memory.conversation_memory_items), 1)
            system.shutdown()

    def test_failed_checkpoint_restores_last_committed_conversation_memory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "yamicha.sqlite3"
            system = make_system(path, FakeTransport())
            system.receive_text(raw("input-001", "覚えて：保存済み"))
            committed = system.memory.conversation_memory_items
            system.persistence._connection.execute(  # noqa: SLF001
                """
                CREATE TRIGGER reject_stage13_checkpoint
                BEFORE INSERT ON checkpoints
                BEGIN
                    SELECT RAISE(ABORT, 'simulated stage-13 interruption');
                END
                """
            )

            with self.assertRaises(PersistenceCommitError):
                system.receive_text(raw("input-002", "覚えて：未保存", 1))
            system.abandon()
            connection = sqlite3.connect(path)
            connection.execute("DROP TRIGGER reject_stage13_checkpoint")
            connection.commit()
            connection.close()

            restored = make_system(path, FakeTransport())
            self.assertEqual(restored.memory.conversation_memory_items, committed)
            restored.shutdown()

    def test_ollama_receives_memory_as_separate_untrusted_system_material(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transport = FakeTransport()
            system = make_system(Path(directory) / "yamicha.sqlite3", transport)
            system.receive_text(raw("input-001", "コーヒーが苦手だと覚えておいて"))
            system.start_new_dialogue_context(
                ExternalTime(NOW.value + timedelta(seconds=1))
            )
            system.receive_text(
                raw("input-002", "コーヒーについて何を覚えてる？", 2)
            )
            request = transport.requests[-1]
            captured = {}

            def open_request(http_request, timeout):
                captured["body"] = json.loads(http_request.data.decode("utf-8"))
                captured["timeout"] = timeout
                return FakeHTTPResponse(
                    {
                        "model": request.proposal.model,
                        "done": True,
                        "message": {"content": '{"reply":"候補"}'},
                    }
                )

            with patch("urllib.request.urlopen", side_effect=open_request):
                response = OllamaChatAdapter().generate(request)

            self.assertEqual(response.status, IntelligenceResultStatus.SUCCESS)
            messages = captured["body"]["messages"]
            self.assertEqual(messages[1]["role"], "system")
            self.assertIn("未信頼内容", messages[1]["content"])
            self.assertIn("コーヒーが苦手", messages[1]["content"])
            self.assertEqual(messages[-1]["role"], "user")
            system.shutdown()

    def test_ollama_memory_interpretation_uses_a_separate_structured_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transport = FakeTransport(
                (
                    "そうなんですね。",
                    interpreted_memory(
                        action="remember",
                        meaning="紅茶が好き",
                        evidence_quote="紅茶が好き",
                    ),
                )
            )
            system = make_system(Path(directory) / "yamicha.sqlite3", transport)
            outcome = system.receive_text(raw("input-001", "紅茶が好き"))
            request = outcome.memory_interpretation_request
            self.assertIsNotNone(request)
            assert request is not None
            captured = {}

            def open_request(http_request, timeout):
                captured["body"] = json.loads(http_request.data.decode("utf-8"))
                captured["timeout"] = timeout
                return FakeHTTPResponse(
                    {
                        "model": request.proposal.model,
                        "done": True,
                        "message": {
                            "content": json.dumps(
                                interpreted_memory(
                                    action="remember",
                                    meaning="紅茶が好き",
                                    evidence_quote="紅茶が好き",
                                ),
                                ensure_ascii=False,
                            )
                        },
                    }
                )

            with patch("urllib.request.urlopen", side_effect=open_request):
                response = OllamaChatAdapter().generate(request)

            self.assertEqual(response.status, IntelligenceResultStatus.SUCCESS)
            body = captured["body"]
            self.assertIn("action", body["format"]["properties"])
            self.assertIn("記憶の採否", body["messages"][0]["content"])
            self.assertEqual(body["messages"][-1]["content"], "紅茶が好き")
            system.shutdown()


if __name__ == "__main__":
    unittest.main()
