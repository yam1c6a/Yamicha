from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import test_stage16_experience_dialogue as fixtures
from yamicha.adapters.intelligence import OllamaChatAdapter
from yamicha.bootstrap import make_stage16_system
from yamicha.bootstrap.stage10 import Stage10TextInputRunner
from yamicha.contracts import IntelligenceResultStatus


class Stage16IntelligenceBoundaryTest(unittest.TestCase):
    def test_followup_wire_requests_an_answer_and_trace_covers_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            system = make_stage16_system(
                persistence_path=Path(directory) / "followup.sqlite3",
                clock=fixtures.FixedClock(), persistence_time_factory=lambda: fixtures.NOW,
                intelligence_transport=fixtures.FakeTransport(),
            )
            try:
                messages = ("コーヒーを説明して", "経験：直前：確認してから答えてほしかった",
                            "経験確認", "コーヒー豆を説明して", "対象はコーヒー豆で、種類の違いを知りたいです")
                outcomes = [system.receive_text(fixtures.raw(str(index), text, index))
                            for index, text in enumerate(messages, 1)]
                confirmation, followup = outcomes[-2:]
                request = followup.intelligence_request
                self.assertEqual(request.proposal.experience_confirmation_reference,
                                 confirmation.dialogue_output.artifact_id)
                with patch("urllib.request.urlopen") as opened:
                    opened.return_value.__enter__.return_value.read.return_value = json.dumps({
                        "model": request.proposal.model, "done": True,
                        "message": {"content": '{"reply":"豆の種類を説明します。"}'},
                    }).encode("utf-8")
                    result = OllamaChatAdapter().generate(request)
                self.assertEqual(result.status, IntelligenceResultStatus.SUCCESS)
                body = json.loads(opened.call_args.args[0].data.decode("utf-8"))
                policy = next(message["content"] for message in body["messages"]
                              if "有限の表現方針" in message["content"])
                self.assertIn("現在入力はその回答", policy)
                self.assertIn("通常の応答を生成", policy)
                self.assertNotIn("回答可能な通常対話では先に", policy)
                payload = Stage10TextInputRunner._intelligence_input_digest_payload(request.proposal)
                trace = system.persistence.intelligence_trace_records()[-1]
                self.assertEqual(trace.input_digest, hashlib.sha256(payload.encode("utf-8")).hexdigest())
                self.assertNotEqual(payload, Stage10TextInputRunner._intelligence_input_digest_payload(
                    replace(request.proposal, experience_confirmation_reference=None)))
            finally:
                system.shutdown()

    def test_wire_contains_only_selected_confirmed_policy_and_trace_covers_version(self):
        with tempfile.TemporaryDirectory() as directory:
            system = make_stage16_system(
                persistence_path=Path(directory) / "test.sqlite3",
                clock=fixtures.FixedClock(), persistence_time_factory=lambda: fixtures.NOW,
                intelligence_transport=fixtures.FakeTransport(),
            )
            try:
                system.receive_text(fixtures.raw("1", "説明をお願い", 1))
                system.receive_text(fixtures.raw("2", "経験：直前：長すぎた", 2))
                system.receive_text(fixtures.raw("3", "経験確認", 3))
                outcome = system.receive_text(fixtures.raw("4", "続けて説明して", 4))
                request = outcome.intelligence_request
                item = outcome.experience_information.selections[0].item
                captured = {}

                class Response:
                    def __enter__(self):
                        return self

                    def __exit__(self, *args):
                        return False

                    def read(self):
                        return json.dumps({"model": request.proposal.model, "done": True,
                                           "message": {"content": '{"reply":"要点です。"}'}}).encode("utf-8")

                def opened(http_request, **kwargs):
                    captured.update(json.loads(http_request.data.decode("utf-8")))
                    return Response()

                with patch("urllib.request.urlopen", side_effect=opened):
                    result = OllamaChatAdapter().generate(request)

                self.assertEqual(result.status, IntelligenceResultStatus.SUCCESS)
                policies = [message["content"] for message in captured["messages"]
                            if "有限の表現方針" in message["content"]]
                self.assertEqual(len(policies), 1)
                self.assertIn(item.item_id, policies[0])
                self.assertIn('"version": 1', policies[0])
                self.assertIn('"value": "brief"', policies[0])
                self.assertNotIn(item.feedback_source_reference, policies[0])
                self.assertNotIn("経験：直前", str(captured["messages"]))
                self.assertIn("160", captured["messages"][0]["content"])
                payload = Stage10TextInputRunner._intelligence_input_digest_payload(request.proposal)
                trace = system.persistence.intelligence_trace_records()[-1]
                self.assertEqual(trace.input_digest, hashlib.sha256(payload.encode("utf-8")).hexdigest())
                changed_item = replace(item, version=2)
                changed_window = replace(outcome.experience_information, selections=(
                    replace(outcome.experience_information.selections[0], item=changed_item),
                ))
                changed_payload = Stage10TextInputRunner._intelligence_input_digest_payload(
                    replace(request.proposal, experience_information=changed_window)
                )
                self.assertNotEqual(payload, changed_payload)

                with self.assertRaises(ValueError):
                    replace(request.proposal, experience_information=replace(changed_window, context_id="another-context"))
                with self.assertRaises(ValueError):
                    replace(request.proposal, constraints=replace(request.proposal.constraints,
                        allowed_input_scope=("current_verified_text",)))
                with self.assertRaises(ValueError):
                    replace(request.proposal, constraints=replace(request.proposal.constraints,
                        max_input_characters=len(request.proposal.input_text)))
            finally:
                system.shutdown()


if __name__ == "__main__":
    unittest.main()
