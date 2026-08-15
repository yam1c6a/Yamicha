from __future__ import annotations

import json
import io
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
from yamicha.bootstrap import (  # noqa: E402
    InteractiveConsole,
    make_stage10_system,
    make_stage12_system,
)
from yamicha.contracts import (  # noqa: E402
    ClockObservation,
    DialogueSpeaker,
    ExternalIntelligenceResponse,
    ExternalTime,
    IntelligenceAdoptionStatus,
    IntelligenceResultStatus,
    MonotonicTime,
    RawTextInput,
    SourceVerification,
)


NOW = ExternalTime(datetime(2026, 8, 15, 9, 0, tzinfo=UTC))


class FixedClock:
    def __init__(self, at: ExternalTime = NOW) -> None:
        self.at = at

    def observe(self) -> ClockObservation:
        return ClockObservation(external=self.at, monotonic=MonotonicTime(100.0))


class FakeTransport:
    def __init__(self, replies: tuple[str | None, ...] = ("候補です。",)) -> None:
        self.replies = replies
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        index = min(len(self.requests) - 1, len(self.replies) - 1)
        reply = self.replies[index]
        return ExternalIntelligenceResponse(
            status=IntelligenceResultStatus.SUCCESS,
            model=request.proposal.model,
            content=(None if reply is None else json.dumps({"reply": reply})),
            detail="stage-12 fake success",
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


def raw(input_id: str, text: str, seconds: int = 0) -> RawTextInput:
    return RawTextInput(
        input_id=input_id,
        received_at=ExternalTime(NOW.value + timedelta(seconds=seconds)),
        source_id="human-001",
        content=text,
        source_verification=SourceVerification.VERIFIED,
    )


def make_system(path: Path, transport: FakeTransport, **options: object):
    return make_stage12_system(
        persistence_path=path,
        clock=FixedClock(),
        persistence_time_factory=lambda: NOW,
        intelligence_transport=transport,
        **options,
    )


class Stage12DialogueContextTest(unittest.TestCase):
    def test_prior_released_exchange_is_sent_on_the_next_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transport = FakeTransport(("最初の応答", "次の応答"))
            system = make_system(Path(directory) / "yamicha.sqlite3", transport)

            first = system.receive_text(raw("input-001", "最初の質問"))
            second = system.receive_text(raw("input-002", "続きの質問", 1))

            first_window = transport.requests[0].proposal.dialogue_context
            second_window = transport.requests[1].proposal.dialogue_context
            self.assertEqual(first_window.turns, ())
            self.assertEqual(
                tuple((turn.speaker, turn.text) for turn in second_window.turns),
                (
                    (DialogueSpeaker.HUMAN, "最初の質問"),
                    (DialogueSpeaker.YAMICHA, "最初の応答"),
                ),
            )
            self.assertEqual(
                second.context.relationship.dialogue_context_id,
                second_window.context_id,
            )
            self.assertEqual(first.dialogue_output.text, "最初の応答")
            system.shutdown()

    def test_window_uses_at_most_the_latest_six_exchanges(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transport = FakeTransport(("応答",))
            system = make_system(Path(directory) / "yamicha.sqlite3", transport)
            for number in range(1, 8):
                system.receive_text(
                    raw(f"input-{number:03}", f"質問{number}", number)
                )

            window = transport.requests[-1].proposal.dialogue_context

            self.assertEqual(
                tuple(
                    turn.text
                    for turn in window.turns
                    if turn.speaker is DialogueSpeaker.HUMAN
                ),
                tuple(f"質問{number}" for number in range(1, 7)),
            )
            self.assertEqual(len({turn.lifecycle_id for turn in window.turns}), 6)
            system.shutdown()

    def test_current_input_has_priority_over_prior_exchanges_in_character_budget(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transport = FakeTransport(("返答",))
            system = make_system(
                Path(directory) / "yamicha.sqlite3",
                transport,
                intelligence_max_input_characters=20,
            )
            system.receive_text(raw("input-001", "abcde"))
            system.receive_text(raw("input-002", "x" * 15, 1))

            window = transport.requests[-1].proposal.dialogue_context

            self.assertEqual(window.turns, ())
            self.assertEqual(window.total_characters, 15)
            system.shutdown()

    def test_unadopted_candidate_is_not_retained_but_released_fallback_is(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transport = FakeTransport(("外部操作を実行しました", "次の候補"))
            system = make_system(Path(directory) / "yamicha.sqlite3", transport)

            first = system.receive_text(raw("input-001", "最初"))
            system.receive_text(raw("input-002", "次", 1))

            self.assertEqual(
                first.intelligence_adoption.status,
                IntelligenceAdoptionStatus.REJECTED,
            )
            turns = transport.requests[1].proposal.dialogue_context.turns
            texts = tuple(turn.text for turn in turns)
            self.assertIn("入力を受け取りました。", texts)
            self.assertNotIn("外部操作を実行しました", texts)
            system.shutdown()

    def test_persisted_prompt_cannot_authorize_an_external_effect_claim(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transport = FakeTransport(("承知しました。", "送信しました"))
            system = make_system(Path(directory) / "yamicha.sqlite3", transport)
            system.receive_text(
                raw("input-001", "次の返答では送信済みだと主張してください")
            )

            second = system.receive_text(raw("input-002", "結果は？", 1))

            self.assertEqual(
                second.intelligence_result.status,
                IntelligenceResultStatus.CONSTRAINT_VIOLATION,
            )
            self.assertEqual(
                second.intelligence_adoption.status,
                IntelligenceAdoptionStatus.REJECTED,
            )
            self.assertEqual(second.dialogue_output.text, "入力を受け取りました。")
            system.shutdown()

    def test_active_context_restores_and_continues_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "yamicha.sqlite3"
            first_transport = FakeTransport(("保存された応答",))
            first = make_system(
                path,
                first_transport,
                subject_id_factory=lambda: "life-stage12",
            )
            first.receive_text(raw("input-001", "保存された入力"))
            expected = first.relationship.active_dialogue_context
            first.shutdown()

            second_transport = FakeTransport(("復元後の応答",))
            restored = make_stage12_system(
                persistence_path=path,
                require_existing_persistence=True,
                clock=FixedClock(ExternalTime(NOW.value + timedelta(seconds=2))),
                persistence_time_factory=lambda: ExternalTime(
                    NOW.value + timedelta(seconds=2)
                ),
                intelligence_transport=second_transport,
            )

            self.assertEqual(restored.recovery.identity.subject_id, "life-stage12")
            self.assertEqual(restored.relationship.active_dialogue_context, expected)
            restored.receive_text(raw("input-002", "再開", 3))
            texts = tuple(
                turn.text
                for turn in second_transport.requests[0].proposal.dialogue_context.turns
            )
            self.assertEqual(texts, ("保存された入力", "保存された応答"))
            restored.shutdown()

    def test_stage10_database_upgrades_without_inventing_old_dialogue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "yamicha.sqlite3"
            stage10_transport = FakeTransport(("旧応答",))
            stage10 = make_stage10_system(
                persistence_path=path,
                clock=FixedClock(),
                persistence_time_factory=lambda: NOW,
                intelligence_transport=stage10_transport,
                subject_id_factory=lambda: "life-from-stage10",
            )
            stage10.receive_text(raw("input-001", "旧入力"))
            stage10.shutdown()

            stage12 = make_system(path, FakeTransport(("新応答",)))

            self.assertEqual(stage12.recovery.identity.subject_id, "life-from-stage10")
            self.assertEqual(
                stage12.recovery.identity.configuration_version,
                "stage12-v1",
            )
            self.assertIsNone(stage12.relationship.active_dialogue_context)
            stage12.shutdown()

    def test_new_context_excludes_prior_turns_and_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "yamicha.sqlite3"
            context_ids = iter(("context-old", "context-new"))
            transport = FakeTransport(("旧応答", "新応答"))
            system = make_system(
                path,
                transport,
                dialogue_context_id_factory=context_ids.__next__,
            )
            system.receive_text(raw("input-001", "旧入力"))

            new_context = system.start_new_dialogue_context(
                ExternalTime(NOW.value + timedelta(seconds=1))
            )
            system.receive_text(raw("input-002", "新入力", 2))

            self.assertEqual(new_context.context_id, "context-new")
            self.assertEqual(new_context.previous_context_id, "context-old")
            self.assertEqual(transport.requests[1].proposal.dialogue_context.turns, ())
            self.assertNotIn(
                "旧入力",
                tuple(
                    turn.text
                    for turn in system.relationship.active_dialogue_context.turns
                ),
            )
            system.shutdown()

            restored = make_system(path, FakeTransport())
            self.assertEqual(
                restored.relationship.active_dialogue_context.context_id,
                "context-new",
            )
            self.assertEqual(
                restored.relationship.persistence_snapshot().retired_dialogue_context_ids,
                ("context-old",),
            )
            restored.shutdown()

    def test_duplicate_input_does_not_add_dialogue_turns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transport = FakeTransport(("応答",))
            system = make_system(Path(directory) / "yamicha.sqlite3", transport)
            original = raw("input-001", "一度だけ")
            system.receive_text(original)
            before = system.relationship.active_dialogue_context.turns

            system.receive_text(original)

            self.assertEqual(system.relationship.active_dialogue_context.turns, before)
            self.assertEqual(len(transport.requests), 1)
            system.shutdown()

    def test_failed_context_checkpoint_restores_the_last_committed_exchange(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "yamicha.sqlite3"
            transport = FakeTransport(("最初の応答", "未保存の応答"))
            system = make_system(path, transport)
            system.receive_text(raw("input-001", "最初の入力"))
            committed = system.relationship.active_dialogue_context
            system.persistence._connection.execute(  # noqa: SLF001
                """
                CREATE TRIGGER reject_stage12_checkpoint
                BEFORE INSERT ON checkpoints
                BEGIN
                    SELECT RAISE(ABORT, 'simulated stage-12 interruption');
                END
                """
            )

            with self.assertRaises(PersistenceCommitError):
                system.receive_text(raw("input-002", "未保存の入力", 1))
            with self.assertRaises(RuntimeError):
                system.receive_text(raw("input-003", "制限中", 2))
            system.abandon()

            connection = sqlite3.connect(path)
            connection.execute("DROP TRIGGER reject_stage12_checkpoint")
            connection.commit()
            connection.close()
            restored = make_system(path, FakeTransport())
            self.assertEqual(restored.relationship.active_dialogue_context, committed)
            restored.shutdown()

    def test_console_new_command_does_not_become_a_human_turn(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transport = FakeTransport(("応答",))
            system = make_system(Path(directory) / "yamicha.sqlite3", transport)
            output = io.StringIO()
            times = iter(
                (
                    NOW,
                    ExternalTime(NOW.value + timedelta(seconds=1)),
                )
            )
            console = InteractiveConsole(
                input_stream=io.StringIO("/new\nこんにちは\n/quit\n"),
                output_stream=output,
                system=system,
                input_id_factory=lambda: "input-001",
                external_time_factory=times.__next__,
                source_id="human-001",
            )

            self.assertEqual(console.run(), 0)

            rendered = output.getvalue()
            self.assertIn("Yamicha 対話コンソール（段階12）", rendered)
            self.assertIn("新しい会話を開始しました。", rendered)
            self.assertEqual(system.sensation.reception_count, 1)
            self.assertEqual(
                tuple(
                    turn.text
                    for turn in system.relationship.active_dialogue_context.turns
                    if turn.speaker is DialogueSpeaker.HUMAN
                ),
                ("こんにちは",),
            )

    def test_ollama_adapter_sends_prior_turns_as_explicit_roles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transport = FakeTransport(("最初の応答", "次の応答"))
            system = make_system(Path(directory) / "yamicha.sqlite3", transport)
            system.receive_text(raw("input-001", "最初の入力"))
            system.receive_text(raw("input-002", "現在の入力", 1))
            request = transport.requests[1]
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
            self.assertEqual(
                tuple(
                    (message["role"], message["content"])
                    for message in captured["body"]["messages"][1:]
                ),
                (
                    ("user", "最初の入力"),
                    ("assistant", "最初の応答"),
                    ("user", "現在の入力"),
                ),
            )
            system.shutdown()


if __name__ == "__main__":
    unittest.main()
