from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from yamicha.body.persistence import PersistenceCorruptionError  # noqa: E402
from yamicha.bootstrap import make_stage8_system, make_stage9_system  # noqa: E402
from yamicha.contracts import (  # noqa: E402
    CapabilityDispatchStatus,
    CapabilityResultStatus,
    ClockObservation,
    ExternalTime,
    MonotonicTime,
    ProtectionMode,
    READ_ONLY_EXPECTED_EFFECT,
    ReadOnlyToolResult,
)


NOW = ExternalTime(datetime(2026, 8, 13, 12, 0, tzinfo=UTC))


class FixedClock:
    def observe(self) -> ClockObservation:
        return ClockObservation(
            external=NOW,
            monotonic=MonotonicTime(100.0),
        )


class RecordingReader:
    def __init__(self, result: ReadOnlyToolResult) -> None:
        self.result = result
        self.targets: list[str] = []

    def read(self, target: str) -> ReadOnlyToolResult:
        self.targets.append(target)
        return self.result


def tool_result(status: CapabilityResultStatus) -> ReadOnlyToolResult:
    if status is CapabilityResultStatus.SUCCESS:
        return ReadOnlyToolResult(
            status=status,
            content="complete",
            observed_scope="document.txt",
            remaining_scope=None,
            detail="complete read",
        )
    if status is CapabilityResultStatus.PARTIAL_SUCCESS:
        return ReadOnlyToolResult(
            status=status,
            content="partial",
            observed_scope="document.txt:first-half",
            remaining_scope="document.txt:second-half",
            detail="read limit reached",
        )
    if status is CapabilityResultStatus.UNKNOWN:
        return ReadOnlyToolResult(
            status=status,
            content=None,
            observed_scope="document.txt",
            remaining_scope=None,
            detail="connection ended without a final response",
            uncertainty="whether the read completed is unknown",
        )
    return ReadOnlyToolResult(
        status=status,
        content=None,
        observed_scope="no content",
        remaining_scope=None,
        detail="resource was unavailable",
    )


def make_system(path: Path, reader: RecordingReader, **kwargs: object):
    return make_stage9_system(
        persistence_path=path,
        clock=FixedClock(),
        persistence_time_factory=lambda: NOW,
        capability_reader=reader,
        authorized_read_targets=("document.txt",),
        **kwargs,
    )


class Stage9CapabilityTest(unittest.TestCase):
    def test_bounded_file_reader_reads_without_changing_the_resource(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            resource = root / "document.txt"
            resource.write_text("external text", encoding="utf-8")
            system = make_stage9_system(
                persistence_path=root / "yamicha.sqlite3",
                clock=FixedClock(),
                persistence_time_factory=lambda: NOW,
                capability_root=root,
                authorized_read_targets=("document.txt",),
            )

            outcome = system.use_read_capability(
                target="document.txt",
                authority_id="local-operator",
                idempotency_key="real-read-001",
                requested_at=NOW,
            )

            self.assertEqual(outcome.result.content, "external text")
            self.assertEqual(resource.read_text(encoding="utf-8"), "external text")
            system.shutdown()

    def test_integrated_read_request_reaches_capability_and_returns_to_sensation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            reader = RecordingReader(tool_result(CapabilityResultStatus.SUCCESS))
            system = make_system(Path(directory) / "yamicha.sqlite3", reader)

            outcome = system.use_read_capability(
                target="document.txt",
                authority_id="local-operator",
                idempotency_key="read-document-001",
                requested_at=NOW,
            )

            self.assertEqual(outcome.dispatch_status, CapabilityDispatchStatus.EXECUTED)
            self.assertEqual(reader.targets, ["document.txt"])
            self.assertEqual(outcome.request.target, "document.txt")
            self.assertEqual(outcome.request.operation.value, "read_text")
            self.assertEqual(outcome.request.authority_id, "local-operator")
            self.assertEqual(outcome.request.expected_effect, READ_ONLY_EXPECTED_EFFECT)
            self.assertEqual(outcome.request.idempotency_key, "read-document-001")
            self.assertIsNotNone(outcome.result)
            self.assertEqual(len(system.sensation.capability_result_events), 1)
            self.assertEqual(len(system.core.capability_result_events), 1)
            system.shutdown()

    def test_forged_core_request_is_rejected_before_capability_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            reader = RecordingReader(tool_result(CapabilityResultStatus.SUCCESS))
            system = make_system(Path(directory) / "yamicha.sqlite3", reader)
            proposal = system.judgment.propose_read_capability(
                target="document.txt",
                authority_id="local-operator",
                expected_effect=READ_ONLY_EXPECTED_EFFECT,
                idempotency_key="forged-001",
                reason="test",
                proposed_at=NOW,
            )
            issued = system.core.integrate_capability_request(proposal, NOW)
            forged = replace(issued, core_finalization_id="forged-finalization")
            permission = system.capability_permission_observer.observe(forged, NOW)

            gate_outcome = system.external_effect_gate.authorize(
                forged,
                permission,
                NOW,
            )

            self.assertIsNone(gate_outcome.permit)
            self.assertFalse(gate_outcome.duplicate)
            self.assertEqual(reader.targets, [])
            self.assertEqual(system.persistence.capability_execution_records(), ())
            system.shutdown()

    def test_capability_rejects_a_forged_gate_permit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            reader = RecordingReader(tool_result(CapabilityResultStatus.SUCCESS))
            system = make_system(Path(directory) / "yamicha.sqlite3", reader)
            proposal = system.judgment.propose_read_capability(
                target="document.txt",
                authority_id="local-operator",
                expected_effect=READ_ONLY_EXPECTED_EFFECT,
                idempotency_key="permit-001",
                reason="test",
                proposed_at=NOW,
            )
            request = system.core.integrate_capability_request(proposal, NOW)
            permission = system.capability_permission_observer.observe(request, NOW)
            gate_outcome = system.external_effect_gate.authorize(
                request,
                permission,
                NOW,
            )
            assert gate_outcome.permit is not None
            forged_permit = replace(
                gate_outcome.permit,
                permit_id="forged-permit",
            )

            with self.assertRaises(ValueError):
                system.capability.execute(forged_permit, NOW)

            self.assertEqual(reader.targets, [])
            system.shutdown()

    def test_unregistered_authority_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            reader = RecordingReader(tool_result(CapabilityResultStatus.SUCCESS))
            system = make_system(Path(directory) / "yamicha.sqlite3", reader)

            outcome = system.use_read_capability(
                target="document.txt",
                authority_id="unknown-operator",
                idempotency_key="unauthorized-001",
                requested_at=NOW,
            )

            self.assertEqual(outcome.dispatch_status, CapabilityDispatchStatus.REJECTED)
            self.assertEqual(reader.targets, [])
            system.shutdown()

    def test_all_four_result_statuses_are_received_as_sensory_events(self) -> None:
        statuses = tuple(CapabilityResultStatus)
        for index, status in enumerate(statuses):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                reader = RecordingReader(tool_result(status))
                system = make_system(Path(directory) / "yamicha.sqlite3", reader)

                outcome = system.use_read_capability(
                    target="document.txt",
                    authority_id="local-operator",
                    idempotency_key=f"status-{index}",
                    requested_at=NOW,
                )

                self.assertIsNotNone(outcome.result)
                self.assertEqual(outcome.result.status, status)
                self.assertEqual(
                    system.sensation.capability_result_events[0].result.status,
                    status,
                )
                system.shutdown()

    def test_unknown_result_is_not_retried_with_the_same_key_even_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "yamicha.sqlite3"
            first_reader = RecordingReader(tool_result(CapabilityResultStatus.UNKNOWN))
            first = make_system(path, first_reader)
            first_outcome = first.use_read_capability(
                target="document.txt",
                authority_id="local-operator",
                idempotency_key="unknown-001",
                requested_at=NOW,
            )
            repeated = first.use_read_capability(
                target="document.txt",
                authority_id="local-operator",
                idempotency_key="unknown-001",
                requested_at=NOW,
            )
            self.assertEqual(first_outcome.result.status, CapabilityResultStatus.UNKNOWN)
            self.assertEqual(repeated.dispatch_status, CapabilityDispatchStatus.DUPLICATE)
            self.assertEqual(first_reader.targets, ["document.txt"])
            first.shutdown()

            second_reader = RecordingReader(tool_result(CapabilityResultStatus.SUCCESS))
            restored = make_system(path, second_reader)
            after_restart = restored.use_read_capability(
                target="document.txt",
                authority_id="local-operator",
                idempotency_key="unknown-001",
                requested_at=NOW,
            )

            self.assertEqual(
                after_restart.dispatch_status,
                CapabilityDispatchStatus.DUPLICATE,
            )
            self.assertEqual(second_reader.targets, [])
            restored.shutdown()

    def test_protection_blocks_capability_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            reader = RecordingReader(tool_result(CapabilityResultStatus.SUCCESS))
            system = make_system(Path(directory) / "yamicha.sqlite3", reader)
            system.activate_fixed_protection(NOW)

            outcome = system.use_read_capability(
                target="document.txt",
                authority_id="local-operator",
                idempotency_key="protected-001",
                requested_at=NOW,
            )

            self.assertEqual(system.protection_boundary.mode, ProtectionMode.PROTECTED)
            self.assertEqual(outcome.dispatch_status, CapabilityDispatchStatus.REJECTED)
            self.assertEqual(reader.targets, [])
            system.shutdown()

    def test_stage8_database_upgrades_without_changing_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "yamicha.sqlite3"
            stage8 = make_stage8_system(
                persistence_path=path,
                clock=FixedClock(),
                persistence_time_factory=lambda: NOW,
                subject_id_factory=lambda: "life-001",
            )
            stage8.shutdown()

            reader = RecordingReader(tool_result(CapabilityResultStatus.SUCCESS))
            stage9 = make_system(path, reader)

            self.assertEqual(stage9.recovery.identity.subject_id, "life-001")
            self.assertEqual(
                stage9.recovery.identity.configuration_version,
                "stage9-v1",
            )
            stage9.shutdown()

    def test_capability_execution_record_tampering_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "yamicha.sqlite3"
            reader = RecordingReader(tool_result(CapabilityResultStatus.SUCCESS))
            system = make_system(path, reader)
            system.use_read_capability(
                target="document.txt",
                authority_id="local-operator",
                idempotency_key="tamper-001",
                requested_at=NOW,
            )
            system.shutdown()
            connection = sqlite3.connect(path)
            connection.execute(
                "UPDATE capability_executions SET result_status = 'failure'"
            )
            connection.commit()
            connection.close()

            with self.assertRaises(PersistenceCorruptionError):
                make_system(path, reader)


if __name__ == "__main__":
    unittest.main()
