"""Core-owned lifecycle records and candidate routing for stage 6."""

from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from yamicha.contracts import (
    CandidateReview,
    DialogueOutput,
    ExpressionArtifact,
    ExpressionReview,
    ExpressionReviewStatus,
    ExternalTime,
    FinalizationStatus,
    InformationCertainty,
    JudgmentContext,
    JudgmentFinalization,
    JudgmentResult,
    LifecycleRecord,
    RecordEntry,
    RecordKind,
    ResponsibilityId,
    RetentionCandidate,
)
from yamicha.life.stage4 import Stage4Relationship, Stage4State
from yamicha.life.stage5 import Stage5Core

from .memory import Stage6Memory


class Stage6Core(Stage5Core):
    def __init__(
        self,
        *,
        state: Stage4State,
        memory: Stage6Memory,
        relationship: Stage4Relationship,
        request_id_factory: Callable[[], str] | None = None,
        expression_request_id_factory: Callable[[], str] | None = None,
        lifecycle_record_id_factory: Callable[[], str] | None = None,
        record_entry_id_factory: Callable[[], str] | None = None,
    ) -> None:
        super().__init__(
            state=state,
            memory=memory,
            relationship=relationship,
            request_id_factory=request_id_factory,
            expression_request_id_factory=expression_request_id_factory,
        )
        self._stage6_memory = memory
        self._lifecycle_record_id_factory = lifecycle_record_id_factory or (
            lambda: str(uuid4())
        )
        self._record_entry_id_factory = record_entry_id_factory or (
            lambda: str(uuid4())
        )
        self._record_ids: set[str] = set()
        self._entry_ids: set[str] = set()
        self._records: list[LifecycleRecord] = []

    @property
    def lifecycle_records(self) -> tuple[LifecycleRecord, ...]:
        return tuple(self._records)

    def record_completed_lifecycle(
        self,
        *,
        context: JudgmentContext,
        judgment: JudgmentResult,
        finalization: JudgmentFinalization,
        expression: ExpressionArtifact,
        expression_review: ExpressionReview,
        dialogue_output: DialogueOutput,
    ) -> LifecycleRecord:
        lifecycle_id = context.lifecycle_id
        if any(
            value != lifecycle_id
            for value in (
                judgment.lifecycle_id,
                finalization.lifecycle_id,
                expression.lifecycle_id,
                expression_review.lifecycle_id,
                dialogue_output.lifecycle_id,
            )
        ):
            raise ValueError("record sources must belong to one lifecycle")
        if finalization.status is not FinalizationStatus.FINALIZED:
            raise ValueError("only finalized judgment can be recorded as completed")
        if expression_review.status is not ExpressionReviewStatus.ACCEPTED:
            raise ValueError("only Core-accepted expression can be completed")
        if (
            expression_review.artifact_id != expression.artifact_id
            or dialogue_output.artifact_id != expression.artifact_id
        ):
            raise ValueError("expression review and result must reference one artifact")
        occurred_at = context.event.received_at
        entries = (
            self._entry(
                lifecycle_id=lifecycle_id,
                kind=RecordKind.EVENT,
                owner=ResponsibilityId.SENSATION,
                reference=context.event.event_id,
                summary="verified text sensory event was received",
                occurred_at=occurred_at,
            ),
            self._entry(
                lifecycle_id=lifecycle_id,
                kind=RecordKind.JUDGMENT,
                owner=ResponsibilityId.JUDGMENT,
                reference=f"judgment:{lifecycle_id}",
                summary=(
                    f"decision direction finalized as "
                    f"{judgment.selected_direction.value}"
                ),
                occurred_at=occurred_at,
            ),
            self._entry(
                lifecycle_id=lifecycle_id,
                kind=RecordKind.EXPRESSION,
                owner=ResponsibilityId.LANGUAGE,
                reference=expression.artifact_id,
                summary=f"expression mode produced as {expression.mode.value}",
                occurred_at=occurred_at,
            ),
            self._entry(
                lifecycle_id=lifecycle_id,
                kind=RecordKind.RESULT,
                owner=ResponsibilityId.PROTECTION_BOUNDARY,
                reference=dialogue_output.artifact_id,
                summary=(
                    f"dialogue output boundary result was "
                    f"{dialogue_output.status.value}"
                ),
                occurred_at=occurred_at,
            ),
        )
        record = LifecycleRecord(
            record_id=self._unique_id(
                self._lifecycle_record_id_factory,
                self._record_ids,
                "lifecycle record",
            ),
            lifecycle_id=lifecycle_id,
            entries=entries,
            recorded_at=occurred_at,
        )
        self._records.append(record)
        return record

    def route_retention_candidates(
        self,
        candidates: tuple[RetentionCandidate, ...],
    ) -> tuple[CandidateReview, ...]:
        return tuple(
            self._stage6_memory.review_candidate(candidate)
            for candidate in candidates
        )

    def _entry(
        self,
        *,
        lifecycle_id: str,
        kind: RecordKind,
        owner: ResponsibilityId,
        reference: str,
        summary: str,
        occurred_at: ExternalTime,
    ) -> RecordEntry:
        return RecordEntry(
            entry_id=self._unique_id(
                self._record_entry_id_factory,
                self._entry_ids,
                "record entry",
            ),
            lifecycle_id=lifecycle_id,
            kind=kind,
            source_owner=owner,
            source_reference=reference,
            summary=summary,
            occurred_at=occurred_at,
            certainty=InformationCertainty.CONFIRMED,
        )

    @staticmethod
    def _unique_id(
        factory: Callable[[], str],
        used: set[str],
        label: str,
    ) -> str:
        value = factory()
        if not value.strip() or value in used:
            raise ValueError(f"{label} ID must be non-empty and unique")
        used.add(value)
        return value
