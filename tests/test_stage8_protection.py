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
from yamicha.body.protection_boundary import ProtectionActiveError  # noqa: E402
from yamicha.bootstrap import make_stage7_system, make_stage8_system  # noqa: E402
from yamicha.contracts import (  # noqa: E402
    ClockObservation,
    ExternalRepairRequest,
    ExternalTime,
    IndependentReleaseVerification,
    InputDisposition,
    MonotonicTime,
    OutputReleaseStatus,
    ProtectionMode,
    ProtectionReleaseProposal,
    ProtectionReleaseRequest,
    RawTextInput,
    RecoveryEvidenceSource,
    RecoveryObservation,
    SourceVerification,
)


NOW = ExternalTime(datetime(2026, 8, 13, 10, 0, tzinfo=UTC))


class FixedClock:
    def observe(self) -> ClockObservation:
        return ClockObservation(
            external=NOW,
            monotonic=MonotonicTime(100.0),
        )


def text(input_id: str, source_id: str = "human-001") -> RawTextInput:
    return RawTextInput(
        input_id=input_id,
        received_at=NOW,
        source_id=source_id,
        content="こんにちは",
        source_verification=SourceVerification.VERIFIED,
    )


def make_system(path: Path, **kwargs: object):
    return make_stage8_system(
        persistence_path=path,
        clock=FixedClock(),
        persistence_time_factory=lambda: NOW,
        **kwargs,
    )


def release_request(
    system,
    activation_id: str,
    *,
    complete: bool,
) -> ProtectionReleaseRequest:
    if complete:
        observations = (
            system.body_recovery_observer.observe(
                healthy=True,
                fact="body recovery was observed",
                uncertainty=None,
                observed_at=NOW,
            ),
            system.state_recovery_observer.observe(
                healthy=True,
                fact="state recovery was observed",
                uncertainty=None,
                observed_at=NOW,
            ),
            system.organ_recovery_observer.observe(
                healthy=True,
                fact="affected organ recovery was observed",
                uncertainty=None,
                observed_at=NOW,
            ),
        )
        evaluation = system.judgment.evaluate_protection_release(
            activation_id=activation_id,
            definition_version="stage8-protection-v1",
            observations=observations,
            evaluated_at=NOW,
        )
        proposal = system.core.finalize_protection_release(evaluation, NOW)
        verification = system.independent_release_verifier.verify(
            proposal=proposal,
            observations=observations,
            verified_at=NOW,
        )
        return ProtectionReleaseRequest(
            request_id="release-request-001",
            proposal=proposal,
            observations=observations,
            verification=verification,
            requested_at=NOW,
        )
    sources = (
        RecoveryEvidenceSource.BODY,
    )
    observations = tuple(
        RecoveryObservation(
            observation_id=f"recovery-{source.value}",
            source=source,
            healthy=True,
            fact=f"{source.value} recovery was observed",
            uncertainty=None,
            observed_at=NOW,
        )
        for source in sources
    )
    return ProtectionReleaseRequest(
        request_id="release-request-001",
        proposal=ProtectionReleaseProposal(
            proposal_id="release-proposal-001",
            activation_id=activation_id,
            protection_definition_version="stage8-protection-v1",
            judgment_approval_id="judgment-release-001",
            core_finalization_id="core-release-001",
            created_at=NOW,
        ),
        observations=observations,
        verification=IndependentReleaseVerification(
            verification_id="verification-001",
            verifier="independent-protection-release-verifier",
            activation_id=activation_id,
            passed=True,
            verified_at=NOW,
        ),
        requested_at=NOW,
    )


class Stage8ProtectionTest(unittest.TestCase):
    def test_authorized_input_is_audited_without_storing_input_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            system = make_system(Path(directory) / "yamicha.sqlite3")

            outcome = system.receive_text(text("input-001"))
            audits = system.persistence.protection_audit_records()

            self.assertEqual(outcome.disposition, InputDisposition.ACCEPTED)
            self.assertTrue(any(record.target == "text-input" for record in audits))
            self.assertTrue(
                any(record.target == "persistence-update" for record in audits)
            )
            self.assertNotIn("こんにちは", " ".join(record.reason for record in audits))
            system.shutdown()

    def test_verified_but_unauthorized_source_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            system = make_system(Path(directory) / "yamicha.sqlite3")

            outcome = system.receive_text(text("input-001", "unknown-human"))

            self.assertEqual(outcome.disposition, InputDisposition.UNAUTHORIZED)
            self.assertEqual(system.sensation.reception_count, 0)
            system.shutdown()

    def test_fixed_operation_is_one_atomic_transition_and_blocks_normal_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            system = make_system(Path(directory) / "yamicha.sqlite3")
            completed = system.receive_text(text("input-before-protection"))
            assert completed.expression is not None
            assert completed.expression_review is not None
            committed_sequence = system.persistence.latest_sequence

            result = system.activate_fixed_protection(NOW)

            self.assertEqual(result.previous_mode, ProtectionMode.NORMAL)
            self.assertEqual(result.current_mode, ProtectionMode.PROTECTED)
            self.assertEqual(system.protection_boundary.mode, ProtectionMode.PROTECTED)
            blocked = system.receive_text(text("input-001"))
            self.assertEqual(blocked.disposition, InputDisposition.BLOCKED)
            blocked_output = system.protection_boundary.release_dialogue_output(
                completed.expression,
                completed.expression_review,
            )
            self.assertEqual(blocked_output.status, OutputReleaseStatus.BLOCKED)
            self.assertEqual(system.persistence.latest_sequence, committed_sequence)
            with self.assertRaises(ProtectionActiveError):
                system.run_time_cycle()
            system.shutdown()

    def test_multistep_request_cannot_be_disguised_as_fixed_operation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            system = make_system(Path(directory) / "yamicha.sqlite3")
            observation = (
                system.fixed_observer.observe_normal_authority_unavailable(NOW)
            )
            reservation = system.fixed_counter.reserve(observation)
            assert reservation is not None
            request = system.fixed_request_factory.make(observation, reservation)
            disguised = replace(
                request,
                procedure=("first-step", "second-step"),
            )

            permit = system.protection_boundary.authorize_fixed_inward_operation(
                disguised
            )

            self.assertIsNone(permit)
            self.assertEqual(system.protection_boundary.mode, ProtectionMode.NORMAL)
            system.shutdown()

    def test_external_repair_is_not_treated_as_inward_or_as_release(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            system = make_system(Path(directory) / "yamicha.sqlite3")
            activation = system.activate_fixed_protection(NOW)

            accepted = system.request_external_repair(
                ExternalRepairRequest(
                    request_id="repair-001",
                    destination="external-maintainer",
                    requested_operation="restart-service",
                    requested_at=NOW,
                )
            )

            self.assertFalse(accepted)
            self.assertEqual(system.protection_boundary.mode, ProtectionMode.PROTECTED)
            self.assertEqual(
                system.protection_boundary.activation_id,
                activation.activation_id,
            )
            system.shutdown()

    def test_single_source_or_core_proposal_alone_cannot_release(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            system = make_system(Path(directory) / "yamicha.sqlite3")
            activation = system.activate_fixed_protection(NOW)

            released = system.release_protection(
                release_request(system, activation.activation_id, complete=False)
            )

            self.assertFalse(released)
            self.assertEqual(system.protection_boundary.mode, ProtectionMode.PROTECTED)
            system.shutdown()

    def test_forged_complete_evidence_cannot_release(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            system = make_system(Path(directory) / "yamicha.sqlite3")
            activation = system.activate_fixed_protection(NOW)
            forged_observations = tuple(
                RecoveryObservation(
                    observation_id=f"forged-{source.value}",
                    source=source,
                    healthy=True,
                    fact="claimed recovery",
                    uncertainty=None,
                    observed_at=NOW,
                )
                for source in RecoveryEvidenceSource
            )
            forged = ProtectionReleaseRequest(
                request_id="forged-release",
                proposal=ProtectionReleaseProposal(
                    proposal_id="forged-proposal",
                    activation_id=activation.activation_id,
                    protection_definition_version="stage8-protection-v1",
                    judgment_approval_id="forged-judgment",
                    core_finalization_id="forged-core",
                    created_at=NOW,
                ),
                observations=forged_observations,
                verification=IndependentReleaseVerification(
                    verification_id="forged-verification",
                    verifier="independent-protection-release-verifier",
                    activation_id=activation.activation_id,
                    passed=True,
                    verified_at=NOW,
                ),
                requested_at=NOW,
            )

            self.assertFalse(system.release_protection(forged))
            self.assertEqual(system.protection_boundary.mode, ProtectionMode.PROTECTED)
            system.shutdown()

    def test_independent_release_requires_all_evidence_and_preserves_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            system = make_system(Path(directory) / "yamicha.sqlite3")
            activation = system.activate_fixed_protection(NOW)

            released = system.release_protection(
                release_request(system, activation.activation_id, complete=True)
            )

            self.assertTrue(released)
            self.assertEqual(system.protection_boundary.mode, ProtectionMode.NORMAL)
            audits = system.persistence.protection_audit_records()
            self.assertTrue(any(record.kind.value == "activation" for record in audits))
            self.assertGreaterEqual(
                sum(record.kind.value == "release" for record in audits),
                2,
            )
            system.shutdown()

    def test_protected_state_survives_restart_independently_of_normal_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "yamicha.sqlite3"
            first = make_system(path, subject_id_factory=lambda: "life-001")
            first.receive_text(text("input-001"))
            activation = first.activate_fixed_protection(NOW)
            first.shutdown()

            restored = make_system(path)

            self.assertEqual(restored.recovery.identity.subject_id, "life-001")
            self.assertEqual(restored.protection_boundary.mode, ProtectionMode.PROTECTED)
            self.assertEqual(
                restored.protection_boundary.activation_id,
                activation.activation_id,
            )
            restored.shutdown()

    def test_audit_tampering_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "yamicha.sqlite3"
            system = make_system(path)
            system.activate_fixed_protection(NOW)
            system.shutdown()
            connection = sqlite3.connect(path)
            connection.execute(
                "UPDATE protection_audit SET reason = 'tampered' WHERE sequence = 1"
            )
            connection.commit()
            connection.close()
            with self.assertRaises(PersistenceCorruptionError):
                make_system(path)

    def test_protection_control_tampering_is_detected_on_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "yamicha.sqlite3"
            system = make_system(path)
            system.activate_fixed_protection(NOW)
            system.shutdown()
            connection = sqlite3.connect(path)
            connection.execute(
                "UPDATE protection_control SET mode = 'normal', activation_id = NULL"
            )
            connection.commit()
            connection.close()

            with self.assertRaises(PersistenceCorruptionError):
                make_system(path)

    def test_stage7_database_is_upgraded_without_changing_life_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "yamicha.sqlite3"
            stage7 = make_stage7_system(
                persistence_path=path,
                clock=FixedClock(),
                persistence_time_factory=lambda: NOW,
                subject_id_factory=lambda: "life-001",
            )
            stage7.receive_text(text("input-001"))
            expected_memory = stage7.memory.memory_items
            stage7.shutdown()

            stage8 = make_system(path)

            self.assertEqual(stage8.recovery.identity.subject_id, "life-001")
            self.assertEqual(
                stage8.recovery.identity.configuration_version,
                "stage8-v1",
            )
            self.assertEqual(stage8.memory.memory_items, expected_memory)
            stage8.shutdown()


if __name__ == "__main__":
    unittest.main()
