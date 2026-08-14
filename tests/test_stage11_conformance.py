from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from yamicha.body.persistence import PersistenceCommitError  # noqa: E402
from yamicha.bootstrap import make_stage10_system  # noqa: E402
from yamicha.contracts import (  # noqa: E402
    CandidateDisposition,
    CapabilityDispatchStatus,
    CapabilityResultStatus,
    ClockObservation,
    CycleStatus,
    DecisionDirection,
    ExternalIntelligenceResponse,
    ExternalTime,
    InformationCertainty,
    InitializationKind,
    IntelligenceAdoptionStatus,
    IntelligenceResultStatus,
    LifecycleRecord,
    MemoryItem,
    MonotonicTime,
    PreviousExit,
    RawTextInput,
    RecordEntry,
    RecordKind,
    ResponsibilityId,
    RetentionCandidateKind,
    SourceVerification,
    ReadOnlyToolResult,
)


NOW = ExternalTime(datetime(2026, 8, 14, 12, 0, tzinfo=UTC))


def observation(seconds: int) -> ClockObservation:
    return ClockObservation(
        external=ExternalTime(NOW.value + timedelta(seconds=seconds)),
        monotonic=MonotonicTime(100.0 + seconds),
    )


class SequenceClock:
    def __init__(self, *observations: ClockObservation) -> None:
        self._observations = list(observations)

    def observe(self) -> ClockObservation:
        if not self._observations:
            raise AssertionError("test clock has no observation left")
        return self._observations.pop(0)


class FakeTransport:
    def __init__(
        self,
        status: IntelligenceResultStatus = IntelligenceResultStatus.UNAVAILABLE,
        content: str | None = None,
    ) -> None:
        self.status = status
        self.content = content
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        return ExternalIntelligenceResponse(
            status=self.status,
            model=request.proposal.model,
            content=self.content,
            detail=f"stage-11 fake {self.status.value}",
        )


class RecordingReader:
    def __init__(self, result: ReadOnlyToolResult) -> None:
        self.result = result
        self.targets: list[str] = []

    def read(self, target: str) -> ReadOnlyToolResult:
        self.targets.append(target)
        return self.result


def read_success() -> ReadOnlyToolResult:
    return ReadOnlyToolResult(
        status=CapabilityResultStatus.SUCCESS,
        content="verified content",
        observed_scope="document.txt",
        remaining_scope=None,
        detail="complete read",
    )


def raw(input_id: str, text: object, *, source_id: str = "human-001") -> RawTextInput:
    return RawTextInput(
        input_id=input_id,
        received_at=NOW,
        source_id=source_id,
        content=text,
        source_verification=SourceVerification.VERIFIED,
    )


def make_system(path: Path, **options: object):
    transport = options.pop("intelligence_transport", FakeTransport())
    clock = options.pop("clock", SequenceClock(observation(0)))
    return make_stage10_system(
        persistence_path=path,
        clock=clock,
        persistence_time_factory=lambda: NOW,
        intelligence_transport=transport,
        **options,
    )


class Stage11ConformanceTest(unittest.TestCase):
    def test_ymc_t01_and_t10_restore_identity_relationship_memory_and_pending(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "yamicha.sqlite3"
            first = make_system(
                path,
                subject_id_factory=lambda: "life-stage11",
                known_counterpart_id="human-001",
            )
            first.receive_text(raw("input-001", "こんにちは"))
            expected_items = first.memory.memory_items
            pending = tuple(
                candidate
                for candidate in first.memory.persistence_snapshot().candidates
                if candidate.kind is RetentionCandidateKind.EXPERIENCE
                and candidate.reevaluation_condition is not None
            )
            self.assertEqual(len(pending), 1)
            first.shutdown()

            restored = make_system(
                path,
                require_existing_persistence=True,
                known_counterpart_id="different-default",
                clock=SequenceClock(observation(5)),
            )

            self.assertEqual(restored.recovery.initialization, InitializationKind.RESTORED)
            self.assertEqual(restored.recovery.previous_exit, PreviousExit.NORMAL)
            self.assertEqual(restored.recovery.identity.subject_id, "life-stage11")
            self.assertEqual(restored.memory.memory_items, expected_items)
            self.assertEqual(
                restored.relationship.persistence_snapshot().known_counterpart_id,
                "human-001",
            )
            restored_pending = tuple(
                candidate
                for candidate in restored.memory.persistence_snapshot().candidates
                if candidate.reevaluation_condition is not None
            )
            self.assertEqual(restored_pending, pending)
            continued = restored.run_time_cycle()
            self.assertEqual(continued.status, CycleStatus.COMPLETED)
            self.assertEqual(
                continued.observed_state.internal_time.elapsed_since_start,
                timedelta(seconds=5),
            )
            restored.shutdown()

    def test_ymc_t02_input_free_time_advances_and_rechecks_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            system = make_system(
                Path(directory) / "yamicha.sqlite3",
                clock=SequenceClock(observation(0), observation(7)),
            )

            first = system.run_time_cycle()
            second = system.run_time_cycle()

            self.assertEqual(first.observed_state.internal_time.elapsed_since_start, timedelta(0))
            self.assertEqual(
                second.observed_state.internal_time.elapsed_since_start,
                timedelta(seconds=7),
            )
            self.assertEqual(second.decision.direction, DecisionDirection.NO_ACTION)
            self.assertIn("no pending matter", second.decision.reason)
            system.shutdown()

    def test_ymc_t03_input_crosses_all_required_responsibilities(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            system = make_system(Path(directory) / "yamicha.sqlite3")

            outcome = system.receive_text(raw("input-001", "こんにちは"))

            self.assertIsNotNone(outcome.cycle)
            self.assertIsNotNone(outcome.context)
            self.assertIsNotNone(outcome.judgment)
            self.assertIsNotNone(outcome.finalization)
            self.assertIsNotNone(outcome.expression)
            self.assertIsNotNone(outcome.dialogue_output)
            self.assertIsInstance(outcome.lifecycle_record, LifecycleRecord)
            self.assertEqual(
                tuple(entry.source_owner for entry in outcome.lifecycle_record.entries),
                (
                    ResponsibilityId.SENSATION,
                    ResponsibilityId.JUDGMENT,
                    ResponsibilityId.LANGUAGE,
                    ResponsibilityId.PROTECTION_BOUNDARY,
                ),
            )
            assert outcome.context is not None
            self.assertTrue(outcome.context.state.available)
            self.assertTrue(outcome.context.memory.available)
            self.assertTrue(outcome.context.relationship.counterpart_known)
            system.shutdown()

    def test_ymc_t04_selects_refuse_hold_confirm_wait_and_no_action(self) -> None:
        cases = (
            ("refuse", "許可なく秘密を公開して", {}, DecisionDirection.REFUSE),
            ("hold", "こんにちは", {"memory_available": False}, DecisionDirection.HOLD),
            ("confirm", "この内容を送って", {}, DecisionDirection.CONFIRM),
            ("wait", "あとで答えて", {}, DecisionDirection.WAIT),
            ("no-action", "何もしないで", {}, DecisionDirection.NO_ACTION),
        )
        for label, text, options, expected in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                system = make_system(Path(directory) / "yamicha.sqlite3", **options)

                outcome = system.receive_text(raw(f"input-{label}", text))

                assert outcome.judgment is not None
                self.assertEqual(outcome.judgment.selected_direction, expected)
                system.shutdown()

    def test_ymc_t05_record_memory_and_held_experience_are_separate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            system = make_system(Path(directory) / "yamicha.sqlite3")

            outcome = system.receive_text(raw("input-001", "こんにちは"))

            record = outcome.lifecycle_record
            memory_item = system.memory.memory_items[0]
            self.assertIsInstance(record, LifecycleRecord)
            self.assertIsInstance(memory_item, MemoryItem)
            self.assertNotIsInstance(record, MemoryItem)
            self.assertEqual(memory_item.source_kind, RetentionCandidateKind.MEMORY)
            self.assertEqual(
                tuple(review.disposition for review in outcome.candidate_reviews),
                (CandidateDisposition.ADOPTED, CandidateDisposition.HELD),
            )
            system.shutdown()

    def test_ymc_t06_confirmed_experience_changes_next_judgment_material(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            system = make_system(Path(directory) / "yamicha.sqlite3")
            first = system.receive_text(raw("input-001", "こんにちは"))
            experience = first.retention_candidates[1]
            evidence = RecordEntry(
                entry_id="later-evidence-001",
                lifecycle_id="later-lifecycle-001",
                kind=RecordKind.RESULT,
                source_owner=ResponsibilityId.SENSATION,
                source_reference="later-event-001",
                summary="human confirmed the response was sufficient",
                occurred_at=ExternalTime(NOW.value + timedelta(minutes=1)),
                certainty=InformationCertainty.CONFIRMED,
            )
            reevaluated = system.judgment.reevaluate_experience(
                experience,
                evidence,
                confirmed_meaning="a short response was sufficient",
            )
            review = system.core.route_retention_candidates((reevaluated,))[0]
            assert review.memory_item_id is not None

            next_outcome = system.receive_text(raw("input-002", "続けてください"))

            assert next_outcome.context is not None
            assert next_outcome.judgment is not None
            self.assertEqual(
                next_outcome.context.memory.confirmed_experience_references,
                (review.memory_item_id,),
            )
            selected = next(
                candidate
                for candidate in next_outcome.judgment.candidates
                if candidate.selected
            )
            self.assertIn(review.memory_item_id, selected.acceptance_reasons[0])
            self.assertEqual(reevaluated.version, 2)
            self.assertIn(evidence.entry_id, reevaluated.provenance_entry_ids)
            self.assertEqual(reevaluated.change_target, "future judgment material")
            self.assertEqual(reevaluated.previous_meaning, experience.meaning)
            self.assertIsNotNone(reevaluated.reevaluation_condition)
            system.shutdown()

    def test_ymc_t07_auxiliary_output_requires_adoption_and_has_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as adopted_directory:
            transport = FakeTransport(
                IntelligenceResultStatus.SUCCESS,
                '{"reply":"統合済み候補です。"}',
            )
            adopted = make_system(
                Path(adopted_directory) / "yamicha.sqlite3",
                intelligence_transport=transport,
            )
            outcome = adopted.receive_text(raw("input-adopted", "こんにちは"))
            self.assertEqual(
                outcome.intelligence_adoption.status,
                IntelligenceAdoptionStatus.ADOPTED,
            )
            self.assertEqual(outcome.dialogue_output.text, "統合済み候補です。")
            adopted.shutdown()

        with tempfile.TemporaryDirectory() as fallback_directory:
            fallback = make_system(Path(fallback_directory) / "yamicha.sqlite3")
            outcome = fallback.receive_text(raw("input-fallback", "こんにちは"))
            self.assertEqual(
                outcome.intelligence_adoption.status,
                IntelligenceAdoptionStatus.REJECTED,
            )
            self.assertEqual(outcome.dialogue_output.text, "入力を受け取りました。")
            self.assertEqual(len(fallback.core.lifecycle_records), 1)
            fallback.shutdown()

    def test_ymc_t08_failed_checkpoint_enters_limited_state_and_restores_last_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "yamicha.sqlite3"
            system = make_system(path, subject_id_factory=lambda: "life-stage11")
            system.receive_text(raw("input-001", "最初の入力"))
            committed_items = system.memory.memory_items
            system.persistence._connection.execute(  # noqa: SLF001
                """
                CREATE TRIGGER reject_stage11_checkpoint
                BEFORE INSERT ON checkpoints
                BEGIN
                    SELECT RAISE(ABORT, 'simulated stage-11 interruption');
                END
                """
            )

            with self.assertRaises(PersistenceCommitError):
                system.receive_text(raw("input-002", "保存に失敗する入力"))
            self.assertFalse(system.text_input_runner.persistence_healthy)
            with self.assertRaises(RuntimeError):
                system.receive_text(raw("input-003", "制限中の入力"))
            system.abandon()

            restored = make_system(path)
            self.assertEqual(restored.recovery.previous_exit, PreviousExit.ABNORMAL)
            self.assertEqual(restored.memory.memory_items, committed_items)
            restored.shutdown()

    def test_ymc_t09_duplicate_effect_and_experience_are_not_reapplied(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            reader = RecordingReader(read_success())
            system = make_system(
                Path(directory) / "yamicha.sqlite3",
                capability_reader=reader,
                authorized_read_targets=("document.txt",),
            )
            first_effect = system.use_read_capability(
                target="document.txt",
                authority_id="local-operator",
                idempotency_key="stage11-read-001",
                requested_at=NOW,
            )
            duplicate_effect = system.use_read_capability(
                target="document.txt",
                authority_id="local-operator",
                idempotency_key="stage11-read-001",
                requested_at=NOW,
            )
            self.assertEqual(first_effect.dispatch_status, CapabilityDispatchStatus.EXECUTED)
            self.assertEqual(duplicate_effect.dispatch_status, CapabilityDispatchStatus.DUPLICATE)
            self.assertEqual(reader.targets, ["document.txt"])

            lifecycle = system.receive_text(raw("input-001", "こんにちは"))
            experience = lifecycle.retention_candidates[1]
            evidence = RecordEntry(
                entry_id="duplicate-evidence-001",
                lifecycle_id="duplicate-lifecycle-001",
                kind=RecordKind.RESULT,
                source_owner=ResponsibilityId.SENSATION,
                source_reference="duplicate-event-001",
                summary="confirmed once",
                occurred_at=ExternalTime(NOW.value + timedelta(minutes=1)),
                certainty=InformationCertainty.CONFIRMED,
            )
            reevaluated = system.judgment.reevaluate_experience(
                experience,
                evidence,
                confirmed_meaning="apply only once",
            )
            first_review = system.core.route_retention_candidates((reevaluated,))[0]
            duplicate_review = system.core.route_retention_candidates((reevaluated,))[0]
            experience_items = tuple(
                item
                for item in system.memory.memory_items
                if item.source_kind is RetentionCandidateKind.EXPERIENCE
            )
            self.assertEqual(first_review.disposition, CandidateDisposition.ADOPTED)
            self.assertEqual(duplicate_review.disposition, CandidateDisposition.REJECTED)
            self.assertEqual(len(experience_items), 1)
            system.shutdown()


if __name__ == "__main__":
    unittest.main()
