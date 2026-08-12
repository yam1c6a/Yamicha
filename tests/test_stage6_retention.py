from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from yamicha.bootstrap import make_stage6_system  # noqa: E402
from yamicha.contracts import (  # noqa: E402
    CandidateDisposition,
    CandidateReviewKind,
    ClockObservation,
    ExternalTime,
    InformationCertainty,
    InputDisposition,
    LifecycleRecord,
    MemoryItem,
    MonotonicTime,
    RawTextInput,
    RecordEntry,
    RecordKind,
    ResponsibilityId,
    RetentionCandidateKind,
    SourceVerification,
)


class OneObservationClock:
    def observe(self) -> ClockObservation:
        return ClockObservation(
            external=ExternalTime(datetime(2026, 8, 12, 16, 0, tzinfo=UTC)),
            monotonic=MonotonicTime(100.0),
        )


def verified_text(input_id: str, text: object) -> RawTextInput:
    return RawTextInput(
        input_id=input_id,
        received_at=ExternalTime(datetime(2026, 8, 12, 16, 0, 1, tzinfo=UTC)),
        source_id="human-001",
        content=text,
        source_verification=SourceVerification.VERIFIED,
    )


def make_system():
    return make_stage6_system(
        clock=OneObservationClock(),
        runtime_id_factory=lambda: "startup-001",
        time_correlation_id_factory=lambda: "startup-cycle-001",
        input_correlation_id_factory=iter(
            ("cycle-001", "cycle-002", "cycle-003")
        ).__next__,
        reception_id_factory=iter(
            ("reception-001", "reception-002", "reception-003")
        ).__next__,
        event_id_factory=iter(
            ("event-001", "event-002", "event-003")
        ).__next__,
        request_id_factory=iter(
            f"request-{number:03}" for number in range(1, 10)
        ).__next__,
        expression_request_id_factory=iter(
            f"expression-request-{number:03}" for number in range(1, 4)
        ).__next__,
        expression_artifact_id_factory=iter(
            f"artifact-{number:03}" for number in range(1, 4)
        ).__next__,
        lifecycle_record_id_factory=iter(
            f"record-{number:03}" for number in range(1, 4)
        ).__next__,
        record_entry_id_factory=iter(
            f"entry-{number:03}" for number in range(1, 13)
        ).__next__,
        retention_candidate_id_factory=iter(
            f"candidate-{number:03}" for number in range(1, 9)
        ).__next__,
        candidate_review_id_factory=iter(
            f"review-{number:03}" for number in range(1, 13)
        ).__next__,
        memory_item_id_factory=iter(
            f"memory-{number:03}" for number in range(1, 5)
        ).__next__,
    )


class Stage6RetentionTest(unittest.TestCase):
    def test_completed_turn_records_event_judgment_expression_and_result(self) -> None:
        system = make_system()

        outcome = system.receive_text(verified_text("input-001", "こんにちは"))

        record = outcome.lifecycle_record
        assert record is not None
        self.assertIsInstance(record, LifecycleRecord)
        self.assertEqual(
            tuple(entry.kind for entry in record.entries),
            (
                RecordKind.EVENT,
                RecordKind.JUDGMENT,
                RecordKind.EXPRESSION,
                RecordKind.RESULT,
            ),
        )
        self.assertEqual(
            tuple(entry.source_owner for entry in record.entries),
            (
                ResponsibilityId.SENSATION,
                ResponsibilityId.JUDGMENT,
                ResponsibilityId.LANGUAGE,
                ResponsibilityId.PROTECTION_BOUNDARY,
            ),
        )
        self.assertTrue(
            all(
                entry.certainty is InformationCertainty.CONFIRMED
                for entry in record.entries
            )
        )
        self.assertEqual(system.core.lifecycle_records, (record,))

    def test_record_memory_and_experience_candidate_remain_distinct(self) -> None:
        system = make_system()

        outcome = system.receive_text(verified_text("input-001", "こんにちは"))

        record = outcome.lifecycle_record
        candidates = outcome.retention_candidates
        reviews = outcome.candidate_reviews
        self.assertIsInstance(record, LifecycleRecord)
        self.assertEqual(
            tuple(candidate.kind for candidate in candidates),
            (RetentionCandidateKind.MEMORY, RetentionCandidateKind.EXPERIENCE),
        )
        self.assertEqual(
            tuple(review.disposition for review in reviews),
            (CandidateDisposition.ADOPTED, CandidateDisposition.HELD),
        )
        self.assertEqual(len(system.memory.memory_items), 1)
        item = system.memory.memory_items[0]
        self.assertIsInstance(item, MemoryItem)
        self.assertNotIsInstance(record, MemoryItem)
        self.assertEqual(item.source_kind, RetentionCandidateKind.MEMORY)
        self.assertIn(candidates[0].candidate_id, item.source_candidate_ids)
        self.assertEqual(candidates[1].certainty, InformationCertainty.UNKNOWN)
        self.assertIsNotNone(candidates[1].reevaluation_condition)

    def test_memory_update_tracks_reason_provenance_time_and_certainty(self) -> None:
        system = make_system()
        outcome = system.receive_text(verified_text("input-001", "こんにちは"))

        item = system.memory.memory_items[0]
        candidate = outcome.retention_candidates[0]

        self.assertIn(candidate.candidate_id, item.update_reason)
        self.assertEqual(item.provenance_entry_ids, candidate.provenance_entry_ids)
        self.assertEqual(item.created_at, candidate.created_at)
        self.assertEqual(item.updated_at, candidate.created_at)
        self.assertEqual(item.certainty, candidate.certainty)

    def test_next_turn_receives_memory_reference_and_repeated_meaning_integrates(self) -> None:
        system = make_system()
        first = system.receive_text(verified_text("input-001", "こんにちは"))
        first_item = system.memory.memory_items[0]

        second = system.receive_text(verified_text("input-002", "もう一度こんにちは"))

        assert second.context is not None
        self.assertIn(
            first_item.memory_item_id,
            second.context.memory.related_references,
        )
        self.assertEqual(
            second.candidate_reviews[0].disposition,
            CandidateDisposition.INTEGRATED,
        )
        self.assertEqual(len(system.memory.memory_items), 1)
        integrated = system.memory.memory_items[0]
        self.assertEqual(integrated.version, 2)
        self.assertEqual(len(integrated.source_candidate_ids), 2)
        assert first.lifecycle_record is not None
        assert second.lifecycle_record is not None
        self.assertEqual(len(system.core.lifecycle_records), 2)

    def test_judgment_proposal_does_not_update_memory_until_core_routes_it(self) -> None:
        system = make_system()
        outcome = system.receive_text(verified_text("input-001", "こんにちは"))
        record = outcome.lifecycle_record
        assert record is not None
        before = system.memory.memory_items

        candidates = system.judgment.propose_retention(record)

        self.assertEqual(system.memory.memory_items, before)
        system.core.route_retention_candidates(candidates)
        self.assertNotEqual(system.memory.memory_items, before)

    def test_experience_candidate_can_be_reevaluated_from_later_evidence(self) -> None:
        system = make_system()
        outcome = system.receive_text(verified_text("input-001", "こんにちは"))
        experience_candidate = outcome.retention_candidates[1]
        later = RecordEntry(
            entry_id="later-result-001",
            lifecycle_id="later-cycle-001",
            kind=RecordKind.RESULT,
            source_owner=ResponsibilityId.SENSATION,
            source_reference="later-event-001",
            summary="human acknowledged the response",
            occurred_at=ExternalTime(
                datetime(2026, 8, 12, 16, 1, tzinfo=UTC)
            ),
            certainty=InformationCertainty.CONFIRMED,
        )

        reevaluated = system.judgment.reevaluate_experience(
            experience_candidate,
            later,
            confirmed_meaning="the short acknowledgement was sufficient",
        )
        review = system.core.route_retention_candidates((reevaluated,))[0]

        self.assertEqual(reevaluated.version, 2)
        self.assertEqual(reevaluated.certainty, InformationCertainty.CONFIRMED)
        self.assertIn(later.entry_id, reevaluated.provenance_entry_ids)
        self.assertEqual(review.kind, CandidateReviewKind.REEVALUATION)
        self.assertEqual(review.disposition, CandidateDisposition.ADOPTED)
        self.assertEqual(len(system.memory.memory_items), 2)
        experience_evidence = system.memory.memory_items[1]
        self.assertEqual(
            experience_evidence.source_kind,
            RetentionCandidateKind.EXPERIENCE,
        )

    def test_stale_or_wrong_owner_candidate_is_rejected(self) -> None:
        system = make_system()
        outcome = system.receive_text(verified_text("input-001", "こんにちは"))
        memory_candidate = outcome.retention_candidates[0]

        stale_review = system.core.route_retention_candidates(
            (memory_candidate,)
        )[0]
        wrong_owner = replace(
            memory_candidate,
            candidate_id="wrong-owner-001",
            proposed_owner=ResponsibilityId.STATE,
            created_at=ExternalTime(
                memory_candidate.created_at.value + timedelta(seconds=1)
            ),
        )
        wrong_owner_review = system.memory.review_candidate(wrong_owner)

        self.assertEqual(
            stale_review.disposition,
            CandidateDisposition.REJECTED,
        )
        self.assertEqual(
            wrong_owner_review.disposition,
            CandidateDisposition.REJECTED,
        )

    def test_invalid_input_creates_neither_record_nor_memory(self) -> None:
        system = make_system()

        outcome = system.receive_text(verified_text("input-001", 123))

        self.assertEqual(outcome.disposition, InputDisposition.INVALID_FORMAT)
        self.assertIsNone(outcome.lifecycle_record)
        self.assertEqual(outcome.retention_candidates, ())
        self.assertEqual(system.core.lifecycle_records, ())
        self.assertEqual(system.memory.memory_items, ())


if __name__ == "__main__":
    unittest.main()
