from __future__ import annotations

import io
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from yamicha.body.runtime import RuntimeStatus  # noqa: E402
from yamicha.bootstrap import InteractiveConsole, make_stage6_system  # noqa: E402
from yamicha.contracts import (  # noqa: E402
    ClockObservation,
    ExternalTime,
    MonotonicTime,
)


class OneObservationClock:
    def observe(self) -> ClockObservation:
        return ClockObservation(
            external=ExternalTime(datetime(2026, 8, 12, 15, 0, tzinfo=UTC)),
            monotonic=MonotonicTime(100.0),
        )


def make_console(input_text: str):
    input_ids = iter(("input-001", "input-002", "input-003", "input-004"))
    correlations = iter(("cycle-001", "cycle-002", "cycle-003", "cycle-004"))
    receptions = iter(
        ("reception-001", "reception-002", "reception-003", "reception-004")
    )
    events = iter(("event-001", "event-002", "event-003", "event-004"))
    requests = iter(f"request-{number:03}" for number in range(1, 13))
    expression_requests = iter(
        f"expression-request-{number:03}" for number in range(1, 5)
    )
    artifacts = iter(f"artifact-{number:03}" for number in range(1, 5))
    times = iter(
        ExternalTime(
            datetime(2026, 8, 12, 15, 0, tzinfo=UTC)
            + timedelta(seconds=number)
        )
        for number in range(1, 5)
    )
    system = make_stage6_system(
        clock=OneObservationClock(),
        runtime_id_factory=lambda: "startup-001",
        time_correlation_id_factory=lambda: "startup-cycle-001",
        input_correlation_id_factory=correlations.__next__,
        reception_id_factory=receptions.__next__,
        event_id_factory=events.__next__,
        request_id_factory=requests.__next__,
        expression_request_id_factory=expression_requests.__next__,
        expression_artifact_id_factory=artifacts.__next__,
        known_counterpart_id="local-console",
    )
    output = io.StringIO()
    console = InteractiveConsole(
        input_stream=io.StringIO(input_text),
        output_stream=output,
        system=system,
        input_id_factory=input_ids.__next__,
        external_time_factory=times.__next__,
        source_id="local-console",
    )
    return console, system, output


class InteractiveConsoleTest(unittest.TestCase):
    def test_multiple_inputs_use_stage6_and_quit_stops_the_system(self) -> None:
        console, system, output = make_console(
            "こんにちは\nこの内容を送って\n何もしないで\n/quit\n"
        )

        result = console.run()

        rendered = output.getvalue()
        self.assertEqual(result, 0)
        self.assertIn("Yamicha 対話コンソール（段階6）", rendered)
        self.assertIn("yamicha: 入力を受け取りました。", rendered)
        self.assertIn("外部への操作はまだ実行していません。", rendered)
        self.assertNotIn("yamicha: None", rendered)
        self.assertEqual(rendered.count("yamicha:"), 2)
        self.assertTrue(rendered.endswith("終了しました。\n"))
        self.assertEqual(system.sensation.reception_count, 3)
        self.assertEqual(system.runtime.status, RuntimeStatus.STOPPED)

    def test_blank_lines_are_ignored_and_eof_exits(self) -> None:
        console, system, output = make_console("\n  \nこんにちは\n")

        result = console.run()

        self.assertEqual(result, 0)
        self.assertEqual(system.sensation.reception_count, 1)
        self.assertIn("yamicha: 入力を受け取りました。", output.getvalue())
        self.assertEqual(system.runtime.status, RuntimeStatus.STOPPED)

    def test_quit_before_input_does_not_start_the_system(self) -> None:
        console, system, output = make_console("/quit\n")

        result = console.run()

        self.assertEqual(result, 0)
        self.assertEqual(system.sensation.reception_count, 0)
        self.assertEqual(system.runtime.status, RuntimeStatus.NOT_STARTED)
        self.assertNotIn("yamicha:", output.getvalue())

    def test_invalid_long_input_reports_an_error_and_keeps_running(self) -> None:
        console, system, output = make_console(f"{'a' * 4097}\nこんにちは\n/quit\n")

        result = console.run()

        rendered = output.getvalue()
        self.assertEqual(result, 0)
        self.assertIn("error: invalid_format", rendered)
        self.assertIn("yamicha: 入力を受け取りました。", rendered)
        self.assertEqual(system.sensation.reception_count, 1)
        self.assertEqual(system.runtime.status, RuntimeStatus.STOPPED)


if __name__ == "__main__":
    unittest.main()
