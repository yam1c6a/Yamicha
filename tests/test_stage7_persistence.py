from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from yamicha.body.persistence import (  # noqa: E402
    PersistenceCommitError,
    PersistenceConsistencyError,
    PersistenceCorruptionError,
    PersistenceMissingError,
)
from yamicha.bootstrap import make_stage7_system  # noqa: E402
from yamicha.contracts import (  # noqa: E402
    ClockObservation,
    ExternalTime,
    InitializationKind,
    MonotonicTime,
    PreviousExit,
    RawTextInput,
    SourceVerification,
)


FIXED_TIME = ExternalTime(datetime(2026, 8, 12, 17, 0, tzinfo=UTC))


class FixedClock:
    def observe(self) -> ClockObservation:
        return ClockObservation(
            external=FIXED_TIME,
            monotonic=MonotonicTime(100.0),
        )


def verified_text(input_id: str, text: str) -> RawTextInput:
    return RawTextInput(
        input_id=input_id,
        received_at=FIXED_TIME,
        source_id="human-001",
        content=text,
        source_verification=SourceVerification.VERIFIED,
    )


def make_system(path: Path, **kwargs: object):
    return make_stage7_system(
        persistence_path=path,
        clock=FixedClock(),
        persistence_time_factory=lambda: FIXED_TIME,
        **kwargs,
    )


class Stage7PersistenceTest(unittest.TestCase):
    def test_normal_restart_restores_same_identity_and_owned_information(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "yamicha.sqlite3"
            first = make_system(
                path,
                subject_id_factory=lambda: "life-001",
                known_counterpart_id="human-001",
                normal_dialogue_output_enabled=False,
            )
            outcome = first.receive_text(verified_text("input-001", "こんにちは"))
            expected_records = first.core.lifecycle_records
            expected_items = first.memory.memory_items
            first.shutdown()

            restored = make_system(
                path,
                known_counterpart_id="different-default",
                normal_dialogue_output_enabled=True,
            )

            self.assertEqual(
                restored.recovery.initialization,
                InitializationKind.RESTORED,
            )
            self.assertEqual(restored.recovery.previous_exit, PreviousExit.NORMAL)
            self.assertEqual(restored.recovery.identity.subject_id, "life-001")
            self.assertEqual(restored.core.lifecycle_records, expected_records)
            self.assertEqual(restored.memory.memory_items, expected_items)
            self.assertEqual(
                restored.relationship.persistence_snapshot().known_counterpart_id,
                "human-001",
            )
            self.assertFalse(
                restored.protection_boundary.persistence_snapshot()
                .normal_dialogue_output_enabled
            )
            assert outcome.lifecycle_record is not None
            continued = restored.receive_text(
                verified_text("input-002", "もう一度こんにちは")
            )
            assert continued.context is not None
            self.assertIn(
                expected_items[0].memory_item_id,
                continued.context.memory.related_references,
            )
            self.assertEqual(len(restored.core.lifecycle_records), 2)
            restored.shutdown()

    def test_abnormal_exit_is_reported_and_last_checkpoint_is_restored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "yamicha.sqlite3"
            first = make_system(path, subject_id_factory=lambda: "life-001")
            first.receive_text(verified_text("input-001", "こんにちは"))
            expected_items = first.memory.memory_items
            first.abandon()

            restored = make_system(path)

            self.assertEqual(restored.recovery.previous_exit, PreviousExit.ABNORMAL)
            self.assertEqual(
                restored.recovery.initialization,
                InitializationKind.RESTORED,
            )
            self.assertEqual(restored.memory.memory_items, expected_items)
            restored.shutdown()

    def test_failed_transaction_does_not_replace_committed_memory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "yamicha.sqlite3"
            first = make_system(path, subject_id_factory=lambda: "life-001")
            first.receive_text(verified_text("input-001", "こんにちは"))
            committed_items = first.memory.memory_items
            first.persistence._connection.execute(  # noqa: SLF001
                """
                CREATE TRIGGER reject_checkpoint
                BEFORE INSERT ON checkpoints
                BEGIN
                    SELECT RAISE(ABORT, 'simulated interruption');
                END
                """
            )

            with self.assertRaises(PersistenceCommitError):
                first.receive_text(
                    verified_text("input-002", "もう一度こんにちは")
                )
            self.assertNotEqual(first.memory.memory_items, committed_items)
            first.abandon()

            restored = make_system(path)

            self.assertEqual(restored.memory.memory_items, committed_items)
            self.assertEqual(restored.persistence.latest_sequence, 2)
            restored.shutdown()

    def test_corrupt_checkpoint_is_not_silently_treated_as_new_life(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "yamicha.sqlite3"
            system = make_system(path, subject_id_factory=lambda: "life-001")
            system.receive_text(verified_text("input-001", "こんにちは"))
            system.shutdown()
            connection = sqlite3.connect(path)
            connection.execute(
                "UPDATE checkpoints SET payload = payload || ' '",
            )
            connection.commit()
            connection.close()

            with self.assertRaises(PersistenceCorruptionError):
                make_system(path)

    def test_configuration_mismatch_prevents_restoration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "yamicha.sqlite3"
            system = make_system(path, subject_id_factory=lambda: "life-001")
            system.receive_text(verified_text("input-001", "こんにちは"))
            system.shutdown()

            with self.assertRaises(PersistenceConsistencyError):
                make_system(path, configuration_version="different-version")

    def test_missing_committed_checkpoint_is_not_reinitialized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "yamicha.sqlite3"
            system = make_system(path, subject_id_factory=lambda: "life-001")
            system.receive_text(verified_text("input-001", "こんにちは"))
            system.shutdown()
            connection = sqlite3.connect(path)
            connection.execute("DELETE FROM checkpoints")
            connection.commit()
            connection.close()

            with self.assertRaises(PersistenceConsistencyError):
                make_system(path)

    def test_missing_required_database_is_explicit_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.sqlite3"

            with self.assertRaises(PersistenceMissingError):
                make_system(path, require_existing_persistence=True)

    def test_database_without_checkpoint_is_initialization_not_restoration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "yamicha.sqlite3"
            first = make_system(path, subject_id_factory=lambda: "life-001")
            self.assertEqual(
                first.recovery.initialization,
                InitializationKind.INITIALIZED,
            )
            self.assertEqual(first.recovery.previous_exit, PreviousExit.NONE)
            first.shutdown()

            reopened = make_system(path)

            self.assertEqual(
                reopened.recovery.initialization,
                InitializationKind.INITIALIZED,
            )
            self.assertEqual(reopened.recovery.previous_exit, PreviousExit.NORMAL)
            self.assertEqual(reopened.recovery.identity.subject_id, "life-001")
            reopened.shutdown()


if __name__ == "__main__":
    unittest.main()
