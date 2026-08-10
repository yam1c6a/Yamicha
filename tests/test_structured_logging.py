from __future__ import annotations

import io
import json
import logging
import sys
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from yamicha.body.runtime import bind_correlation, configure_json_logger  # noqa: E402


class StructuredLoggingTest(unittest.TestCase):
    def test_log_contains_correlation_and_event(self) -> None:
        stream = io.StringIO()
        logger = configure_json_logger(
            "yamicha.test.logging",
            stream=stream,
            level=logging.INFO,
        )
        correlated = bind_correlation(logger, "cycle-001")

        correlated.info("起動しました", extra={"event": "runtime.started"})

        payload = json.loads(stream.getvalue())
        self.assertEqual(payload["correlation_id"], "cycle-001")
        self.assertEqual(payload["event"], "runtime.started")
        self.assertEqual(payload["message"], "起動しました")

    def test_empty_correlation_id_is_rejected(self) -> None:
        logger = configure_json_logger("yamicha.test.empty", stream=io.StringIO())
        with self.assertRaises(ValueError):
            bind_correlation(logger, "  ")


if __name__ == "__main__":
    unittest.main()
