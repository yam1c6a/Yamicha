from __future__ import annotations

import ast
import io
import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from yamicha.adapters.channels import ConsoleChannel  # noqa: E402
from yamicha.bootstrap import make_stage3_system  # noqa: E402
from yamicha.contracts import (  # noqa: E402
    ClockObservation,
    ContentTrust,
    ExternalTime,
    InputCycleStatus,
    InputDisposition,
    InputQuality,
    JudgmentStartRequest,
    MonotonicTime,
    ObservationEvidence,
    ObservationKind,
    RawTextInput,
    RequestStatus,
    ResponsibilityCategory,
    ResponsibilityId,
    ResponsibilityPort,
    SourceVerification,
)


class OneObservationClock:
    def observe(self) -> ClockObservation:
        return ClockObservation(
            external=ExternalTime(datetime(2026, 8, 12, 12, 0, tzinfo=UTC)),
            monotonic=MonotonicTime(100.0),
        )


def external_time() -> ExternalTime:
    return ExternalTime(datetime(2026, 8, 12, 12, 0, 1, tzinfo=UTC))


def verified_text(
    *,
    input_id: str = "input-001",
    content: object = "こんにちは\r\n世界",
    media_type: str = "text/plain",
    verification: SourceVerification = SourceVerification.VERIFIED,
) -> RawTextInput:
    return RawTextInput(
        input_id=input_id,
        received_at=external_time(),
        source_id="human-001",
        content=content,
        media_type=media_type,
        source_verification=verification,
    )


def make_system(*, input_correlations=None, request_ids=None):
    correlations = input_correlations or iter(("input-cycle-001",))
    requests = request_ids or iter(("request-001", "request-002", "request-003"))
    return make_stage3_system(
        clock=OneObservationClock(),
        runtime_id_factory=lambda: "startup-001",
        time_correlation_id_factory=lambda: "startup-cycle-001",
        input_correlation_id_factory=correlations.__next__,
        reception_id_factory=iter(("reception-001", "reception-002")).__next__,
        event_id_factory=iter(("event-001", "event-002")).__next__,
        request_id_factory=requests.__next__,
    )


class Stage3InputRoutingTest(unittest.TestCase):
    def test_stage3_composition_keeps_all_responsibility_boundaries(self) -> None:
        system = make_system()
        responsibilities = system.composition.responsibilities

        self.assertEqual(
            {port.definition.identifier for port in responsibilities},
            set(ResponsibilityId),
        )
        self.assertEqual(
            sum(
                port.definition.category is ResponsibilityCategory.ORGAN
                for port in responsibilities
            ),
            9,
        )
        self.assertTrue(
            all(isinstance(port, ResponsibilityPort) for port in responsibilities)
        )

    def test_verified_text_is_normalized_then_routed_by_core(self) -> None:
        system = make_system()

        outcome = system.receive_text(verified_text())

        self.assertEqual(outcome.disposition, InputDisposition.ACCEPTED)
        self.assertEqual(outcome.correlation_id, "input-cycle-001")
        self.assertIsNotNone(outcome.cycle)
        cycle = outcome.cycle
        assert cycle is not None
        self.assertEqual(cycle.lifecycle_id, outcome.correlation_id)
        self.assertEqual(cycle.status, InputCycleStatus.ROUTED)
        self.assertEqual(cycle.event.content_trust, ContentTrust.UNTRUSTED)
        self.assertEqual(cycle.event.quality, InputQuality.VALID)
        self.assertEqual(cycle.event.meaning.normalized_text, "こんにちは\n世界")
        self.assertEqual(system.sensation.receptions[0].original_text, "こんにちは\r\n世界")
        self.assertEqual(cycle.event.raw_reference, "reception-001")

        destinations = {request.destination for request in cycle.requests}
        self.assertEqual(
            destinations,
            {
                ResponsibilityId.STATE,
                ResponsibilityId.MEMORY,
                ResponsibilityId.RELATIONSHIP,
            },
        )
        self.assertTrue(
            all(request.lifecycle_id == cycle.lifecycle_id for request in cycle.requests)
        )
        self.assertTrue(
            all(request.source is ResponsibilityId.CORE for request in cycle.requests)
        )
        self.assertTrue(
            all(response.status is RequestStatus.SUCCEEDED for response in cycle.responses)
        )
        self.assertTrue(
            all(not response.confirmed_effects for response in cycle.responses)
        )
        for request in cycle.requests:
            self.assertEqual(
                system.core.request_transitions(request.request_id),
                (
                    RequestStatus.RECEIVED,
                    RequestStatus.ACCEPTED,
                    RequestStatus.RUNNING,
                    RequestStatus.SUCCEEDED,
                ),
            )
        self.assertEqual(system.core.judgment_start_count, 0)

    def test_invalid_untrusted_and_unsupported_inputs_do_not_reach_sensation(self) -> None:
        cases = (
            (verified_text(content=123), InputDisposition.INVALID_FORMAT),
            (
                verified_text(verification=SourceVerification.UNVERIFIABLE),
                InputDisposition.UNTRUSTED,
            ),
            (
                verified_text(media_type="application/json"),
                InputDisposition.UNSUPPORTED,
            ),
        )
        for raw, expected in cases:
            with self.subTest(expected=expected):
                system = make_system()
                outcome = system.receive_text(raw)

                self.assertEqual(outcome.disposition, expected)
                self.assertIsNotNone(outcome.rejection)
                self.assertIsNone(outcome.cycle)
                self.assertEqual(system.sensation.reception_count, 0)
                self.assertEqual(system.core.lifecycle_count, 0)

    def test_duplicate_is_marked_and_not_routed_twice(self) -> None:
        system = make_system(
            input_correlations=iter(("input-cycle-001", "input-cycle-002")),
        )
        raw = verified_text()

        first = system.receive_text(raw)
        second = system.receive_text(raw)

        self.assertEqual(first.disposition, InputDisposition.ACCEPTED)
        self.assertEqual(second.disposition, InputDisposition.DUPLICATE)
        self.assertIsNotNone(second.cycle)
        cycle = second.cycle
        assert cycle is not None
        self.assertEqual(cycle.status, InputCycleStatus.DUPLICATE_RECORDED)
        self.assertEqual(cycle.requests, ())
        self.assertEqual(cycle.responses, ())
        self.assertEqual(system.sensation.reception_count, 2)
        self.assertEqual(system.core.lifecycle_count, 2)

    def test_sensation_module_has_no_state_or_judgment_dependency(self) -> None:
        path = (
            REPOSITORY_ROOT
            / "src"
            / "yamicha"
            / "life"
            / "stage3"
            / "sensation.py"
        )
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)

        self.assertFalse(any("state" in module for module in imported))
        self.assertFalse(any("judgment" in module for module in imported))
        self.assertFalse(any("core" in module for module in imported))
        self.assertNotIn("yamicha.life.stage2", imported)

    def test_judgment_start_requires_an_organ_and_observation_evidence(self) -> None:
        system = make_system()
        evidence = ObservationEvidence(
            kind=ObservationKind.INTERNAL_STATE,
            reference="state:evidence-001",
            observed_at=external_time(),
        )
        accepted = system.core.accept_judgment_start(
            JudgmentStartRequest(
                request_id="judgment-start-001",
                lifecycle_id="internal-cycle-001",
                source=ResponsibilityId.STATE,
                purpose="re-evaluate an observed internal condition",
                evidence=evidence,
            )
        )

        self.assertEqual(accepted.status, RequestStatus.ACCEPTED)
        with self.assertRaises(ValueError):
            system.core.accept_judgment_start(
                JudgmentStartRequest(
                    request_id="judgment-start-002",
                    lifecycle_id="internal-cycle-002",
                    source=ResponsibilityId.RUNTIME,
                    purpose="the scheduler fired",
                    evidence=evidence,
                )
            )

    def test_input_correlation_id_cannot_be_reused(self) -> None:
        system = make_system(
            input_correlations=iter(("input-cycle-001", "input-cycle-001")),
        )
        system.receive_text(verified_text(input_id="input-001"))

        with self.assertRaises(ValueError):
            system.receive_text(verified_text(input_id="input-002"))

    def test_console_ui_shows_status_acceptance_and_error(self) -> None:
        output = io.StringIO()
        console = ConsoleChannel(output)
        console.show_status("waiting")
        system = make_system(
            input_correlations=iter(("input-cycle-001", "input-cycle-002")),
        )
        accepted = system.receive_text(
            console.make_text_input(
                input_id="input-001",
                received_at=external_time(),
                text="こんにちは",
            )
        )
        rejected = system.receive_text(verified_text(input_id="", content="失敗"))
        console.show_outcome(accepted)
        console.show_outcome(rejected)

        rendered = output.getvalue()
        self.assertIn("status: waiting", rendered)
        self.assertIn("accepted: input-cycle-001", rendered)
        self.assertIn("error: invalid_format", rendered)


if __name__ == "__main__":
    unittest.main()
