"""Judgment-owned generation and reevaluation of retention candidates."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from uuid import uuid4

from yamicha.contracts import (
    InformationCertainty,
    LifecycleRecord,
    RecordEntry,
    RecordKind,
    ResponsibilityId,
    RetentionCandidate,
    RetentionCandidateKind,
)
from yamicha.life.stage4 import Stage4Judgment


class Stage6Judgment(Stage4Judgment):
    def __init__(
        self,
        *,
        candidate_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._candidate_id_factory = candidate_id_factory or (lambda: str(uuid4()))
        self._candidate_ids: set[str] = set()

    def propose_retention(
        self,
        record: LifecycleRecord,
    ) -> tuple[RetentionCandidate, RetentionCandidate]:
        judgment_entry = next(
            entry for entry in record.entries if entry.kind is RecordKind.JUDGMENT
        )
        result_entry = next(
            entry for entry in record.entries if entry.kind is RecordKind.RESULT
        )
        provenance = tuple(entry.entry_id for entry in record.entries)
        memory_candidate = RetentionCandidate(
            candidate_id=self._next_candidate_id(),
            lifecycle_id=record.lifecycle_id,
            kind=RetentionCandidateKind.MEMORY,
            proposed_owner=ResponsibilityId.MEMORY,
            version=1,
            meaning=judgment_entry.summary,
            reason="retain the confirmed decision trace for later reference",
            provenance_entry_ids=provenance,
            created_at=record.recorded_at,
            certainty=InformationCertainty.CONFIRMED,
            reevaluation_condition=None,
        )
        experience_candidate = RetentionCandidate(
            candidate_id=self._next_candidate_id(),
            lifecycle_id=record.lifecycle_id,
            kind=RetentionCandidateKind.EXPERIENCE,
            proposed_owner=ResponsibilityId.MEMORY,
            version=1,
            meaning=(
                f"later outcome may give meaning to {result_entry.summary}"
            ),
            reason=(
                "an output-boundary result alone does not show how the human or "
                "world received it"
            ),
            provenance_entry_ids=provenance,
            created_at=record.recorded_at,
            certainty=InformationCertainty.UNKNOWN,
            reevaluation_condition="observe a later human or world outcome",
        )
        return memory_candidate, experience_candidate

    def reevaluate_experience(
        self,
        candidate: RetentionCandidate,
        evidence: RecordEntry,
        *,
        confirmed_meaning: str,
    ) -> RetentionCandidate:
        if candidate.kind is not RetentionCandidateKind.EXPERIENCE:
            raise ValueError("only an experience candidate can be reevaluated here")
        if not confirmed_meaning.strip():
            raise ValueError("reevaluation requires a confirmed meaning")
        return replace(
            candidate,
            version=candidate.version + 1,
            meaning=confirmed_meaning,
            reason=(
                f"reevaluated from later evidence {evidence.entry_id} owned by "
                f"{evidence.source_owner.value}"
            ),
            provenance_entry_ids=tuple(
                dict.fromkeys((*candidate.provenance_entry_ids, evidence.entry_id))
            ),
            created_at=evidence.occurred_at,
            certainty=InformationCertainty.CONFIRMED,
            reevaluation_condition=None,
        )

    def _next_candidate_id(self) -> str:
        candidate_id = self._candidate_id_factory()
        if not candidate_id.strip() or candidate_id in self._candidate_ids:
            raise ValueError("retention candidate ID must be non-empty and unique")
        self._candidate_ids.add(candidate_id)
        return candidate_id
