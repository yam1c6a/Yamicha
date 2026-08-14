from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
import urllib.error
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from yamicha.body.persistence import PersistenceCorruptionError  # noqa: E402
from yamicha.adapters.intelligence import OllamaChatAdapter  # noqa: E402
from yamicha.bootstrap import make_stage9_system  # noqa: E402
from yamicha.bootstrap.stage10 import make_stage10_system  # noqa: E402
from yamicha.contracts import (  # noqa: E402
    ClockObservation,
    ExternalIntelligenceResponse,
    ExternalTime,
    IntelligenceAdoptionStatus,
    IntelligenceResultStatus,
    MonotonicTime,
    OutputReleaseStatus,
    RawTextInput,
    SourceVerification,
)


NOW = ExternalTime(datetime(2026, 8, 14, 10, 0, tzinfo=UTC))


class FixedClock:
    def observe(self) -> ClockObservation:
        return ClockObservation(
            external=NOW,
            monotonic=MonotonicTime(100.0),
        )


class FakeTransport:
    def __init__(self, response_factory) -> None:
        self._response_factory = response_factory
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        return self._response_factory(request)


def response(
    status: IntelligenceResultStatus,
    *,
    content: str | None = None,
):
    def make(request):
        return ExternalIntelligenceResponse(
            status=status,
            model=request.proposal.model,
            content=content,
            detail=f"fake {status.value}",
        )

    return make


def raw(input_id: str, content: str = "こんにちは") -> RawTextInput:
    return RawTextInput(
        input_id=input_id,
        received_at=NOW,
        source_id="human-001",
        content=content,
        source_verification=SourceVerification.VERIFIED,
    )


def make_system(path: Path, transport: FakeTransport, **kwargs: object):
    return make_stage10_system(
        persistence_path=path,
        clock=FixedClock(),
        persistence_time_factory=lambda: NOW,
        intelligence_transport=transport,
        **kwargs,
    )


class Stage10IntelligenceTest(unittest.TestCase):
    def test_ollama_adapter_is_local_only_and_reports_connection_failure(self) -> None:
        with self.assertRaises(ValueError):
            OllamaChatAdapter(endpoint="https://example.com/api/chat")
        with tempfile.TemporaryDirectory() as directory:
            transport = FakeTransport(
                response(
                    IntelligenceResultStatus.SUCCESS,
                    content='{"reply":"候補です。"}',
                )
            )
            system = make_system(Path(directory) / "yamicha.sqlite3", transport)
            system.receive_text(raw("input-001"))
            request = transport.requests[0]
            adapter = OllamaChatAdapter()

            with patch(
                "urllib.request.urlopen",
                side_effect=urllib.error.URLError("offline"),
            ):
                result = adapter.generate(request)

            self.assertEqual(result.status, IntelligenceResultStatus.UNAVAILABLE)
            system.shutdown()

    def test_adopted_candidate_passes_judgment_core_language_and_output_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transport = FakeTransport(
                response(
                    IntelligenceResultStatus.SUCCESS,
                    content='{"reply":"こんにちは。今日はどうしましたか？"}',
                )
            )
            system = make_system(Path(directory) / "yamicha.sqlite3", transport)

            outcome = system.receive_text(raw("input-001"))

            self.assertEqual(outcome.dialogue_output.text, "こんにちは。今日はどうしましたか？")
            self.assertTrue(outcome.expression.external_intelligence_used)
            self.assertEqual(
                outcome.intelligence_adoption.status,
                IntelligenceAdoptionStatus.ADOPTED,
            )
            self.assertTrue(
                system.core.issued_intelligence_adoption(
                    outcome.intelligence_adoption
                )
            )
            self.assertEqual(len(system.core.lifecycle_records), 1)
            system.shutdown()

    def test_only_current_verified_text_is_sent_for_the_fixed_purpose(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transport = FakeTransport(
                response(
                    IntelligenceResultStatus.SUCCESS,
                    content='{"reply":"候補です。"}',
                )
            )
            system = make_system(Path(directory) / "yamicha.sqlite3", transport)

            system.receive_text(raw("input-001", "この入力だけを使って"))

            request = transport.requests[0]
            self.assertEqual(request.proposal.input_text, "この入力だけを使って")
            self.assertEqual(
                request.proposal.constraints.allowed_input_scope,
                (
                    "current_verified_text",
                    "verified_speaker_and_model_identity",
                ),
            )
            self.assertEqual(
                request.proposal.purpose.value,
                "dialogue_response_candidate",
            )
            system.shutdown()

    def test_core_rejects_auxiliary_self_identification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transport = FakeTransport(
                response(
                    IntelligenceResultStatus.SUCCESS,
                    content='{"reply":"私はYamichaの補助知能です。"}',
                )
            )
            system = make_system(Path(directory) / "yamicha.sqlite3", transport)

            outcome = system.receive_text(raw("input-001", "あなたは？"))

            self.assertEqual(
                outcome.intelligence_result.status,
                IntelligenceResultStatus.SUCCESS,
            )
            self.assertEqual(
                outcome.intelligence_adoption.status,
                IntelligenceAdoptionStatus.REJECTED,
            )
            self.assertIn("changed verified identity", outcome.intelligence_adoption.reason)
            self.assertEqual(outcome.dialogue_output.text, "入力を受け取りました。")
            system.shutdown()

    def test_identity_question_requires_verified_yamicha_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transport = FakeTransport(
                response(
                    IntelligenceResultStatus.SUCCESS,
                    content='{"reply":"何かお手伝いできますか？"}',
                )
            )
            system = make_system(Path(directory) / "yamicha.sqlite3", transport)

            outcome = system.receive_text(raw("input-001", "あなたは誰？"))

            self.assertEqual(
                outcome.intelligence_adoption.status,
                IntelligenceAdoptionStatus.REJECTED,
            )
            self.assertEqual(outcome.dialogue_output.text, "入力を受け取りました。")
            system.shutdown()

    def test_identity_and_auxiliary_model_answers_must_match_verified_facts(self) -> None:
        cases = (
            (
                "identity",
                "あなたは誰？",
                '{"reply":"私はYamichaです。"}',
            ),
            (
                "model",
                "モデルは？",
                '{"reply":"Yamichaは補助知能としてgemma4:e4b-it-qatを使っています。"}',
            ),
        )
        for label, question, content in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                transport = FakeTransport(
                    response(
                        IntelligenceResultStatus.SUCCESS,
                        content=content,
                    )
                )
                system = make_system(Path(directory) / "yamicha.sqlite3", transport)

                outcome = system.receive_text(raw(f"input-{label}", question))

                self.assertEqual(
                    outcome.intelligence_adoption.status,
                    IntelligenceAdoptionStatus.ADOPTED,
                )
                self.assertEqual(
                    outcome.dialogue_output.text,
                    json.loads(content)["reply"],
                )
                system.shutdown()

    def test_unavailable_timeout_invalid_and_constraint_violation_fall_back(self) -> None:
        cases = (
            (
                "unavailable",
                response(IntelligenceResultStatus.UNAVAILABLE),
                IntelligenceResultStatus.UNAVAILABLE,
            ),
            (
                "timeout",
                response(IntelligenceResultStatus.TIMEOUT),
                IntelligenceResultStatus.TIMEOUT,
            ),
            (
                "invalid",
                response(IntelligenceResultStatus.SUCCESS, content="not-json"),
                IntelligenceResultStatus.INVALID_OUTPUT,
            ),
            (
                "constraint",
                response(
                    IntelligenceResultStatus.SUCCESS,
                    content='{"reply":"外部操作を実行しました"}',
                ),
                IntelligenceResultStatus.CONSTRAINT_VIOLATION,
            ),
        )
        for label, factory, expected in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                transport = FakeTransport(factory)
                system = make_system(Path(directory) / "yamicha.sqlite3", transport)

                outcome = system.receive_text(raw(f"input-{label}"))

                self.assertEqual(outcome.intelligence_result.status, expected)
                self.assertEqual(
                    outcome.intelligence_adoption.status,
                    IntelligenceAdoptionStatus.REJECTED,
                )
                self.assertEqual(outcome.dialogue_output.text, "入力を受け取りました。")
                self.assertFalse(outcome.expression.external_intelligence_used)
                self.assertEqual(len(system.core.lifecycle_records), 1)
                system.shutdown()

    def test_forged_adoption_cannot_release_external_intelligence_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transport = FakeTransport(
                response(
                    IntelligenceResultStatus.SUCCESS,
                    content='{"reply":"正規候補です。"}',
                )
            )
            system = make_system(Path(directory) / "yamicha.sqlite3", transport)
            outcome = system.receive_text(raw("input-001"))
            forged = replace(
                outcome.intelligence_adoption,
                adoption_id="forged-adoption",
            )
            artifact = system.language.express_adopted_candidate(
                outcome.expression_request,
                outcome.intelligence_result,
                forged,
            )

            review = system.core.review_intelligence_expression(
                outcome.expression_request,
                artifact,
                forged,
                outcome.intelligence_result,
            )
            released = system.protection_boundary.release_dialogue_output(
                artifact,
                review,
            )

            self.assertEqual(released.status, OutputReleaseStatus.BLOCKED)
            system.shutdown()

    def test_trace_records_purpose_constraints_result_and_adoption_without_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "yamicha.sqlite3"
            input_text = "保存しない入力本文"
            output_text = "保存しない応答候補"
            transport = FakeTransport(
                response(
                    IntelligenceResultStatus.SUCCESS,
                    content=f'{{"reply":"{output_text}"}}',
                )
            )
            system = make_system(path, transport)

            system.receive_text(raw("input-001", input_text))
            traces = system.persistence.intelligence_trace_records()

            self.assertEqual(len(traces), 1)
            self.assertEqual(traces[0].purpose.value, "dialogue_response_candidate")
            self.assertEqual(traces[0].result_status, IntelligenceResultStatus.SUCCESS)
            self.assertEqual(
                traces[0].adoption_status,
                IntelligenceAdoptionStatus.ADOPTED,
            )
            self.assertEqual(
                traces[0].input_scope,
                (
                    "current_verified_text",
                    "verified_speaker_and_model_identity",
                ),
            )
            self.assertNotEqual(traces[0].input_digest, input_text)
            self.assertNotEqual(traces[0].output_digest, output_text)
            system.shutdown()
            database_bytes = path.read_bytes()
            self.assertNotIn(input_text.encode("utf-8"), database_bytes)
            self.assertNotIn(output_text.encode("utf-8"), database_bytes)

    def test_stage9_database_upgrades_without_changing_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "yamicha.sqlite3"
            stage9 = make_stage9_system(
                persistence_path=path,
                clock=FixedClock(),
                persistence_time_factory=lambda: NOW,
                subject_id_factory=lambda: "life-001",
            )
            stage9.shutdown()
            transport = FakeTransport(response(IntelligenceResultStatus.UNAVAILABLE))

            stage10 = make_system(path, transport)

            self.assertEqual(stage10.recovery.identity.subject_id, "life-001")
            self.assertEqual(
                stage10.recovery.identity.configuration_version,
                "stage10-v1",
            )
            stage10.shutdown()

    def test_intelligence_trace_tampering_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "yamicha.sqlite3"
            transport = FakeTransport(
                response(
                    IntelligenceResultStatus.SUCCESS,
                    content='{"reply":"候補です。"}',
                )
            )
            system = make_system(path, transport)
            system.receive_text(raw("input-001"))
            system.shutdown()
            connection = sqlite3.connect(path)
            connection.execute(
                "UPDATE intelligence_trace SET adoption_status = 'rejected'"
            )
            connection.commit()
            connection.close()

            with self.assertRaises(PersistenceCorruptionError):
                make_system(path, transport)


if __name__ == "__main__":
    unittest.main()
