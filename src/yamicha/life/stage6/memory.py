"""Memory-owned review and in-memory retention for stage 6."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from uuid import uuid4

from yamicha.contracts import (
    CandidateDisposition,
    CandidateReview,
    CandidateReviewKind,
    InformationCertainty,
    MemoryDecisionMaterial,
    MemoryItem,
    ResponsibilityId,
    RetentionCandidate,
    RetentionCandidateKind,
    SensoryEvent,
)
from yamicha.life.stage4 import Stage4Memory


class Stage6Memory(Stage4Memory):
    def __init__(
        self,
        *,
        available: bool = True,
        review_id_factory: Callable[[], str] | None = None,
        memory_item_id_factory: Callable[[], str] | None = None,
    ) -> None:
        super().__init__(available=available)
        self._review_id_factory = review_id_factory or (lambda: str(uuid4()))
        self._memory_item_id_factory = memory_item_id_factory or (
            lambda: str(uuid4())
        )
        self._review_ids: set[str] = set()
        self._memory_item_ids: set[str] = set()
        self._latest_candidate_versions: dict[str, int] = {}
        self._candidates: dict[str, RetentionCandidate] = {}
        self._reviews: list[CandidateReview] = []
        self._items: dict[str, MemoryItem] = {}
        self._material_version = 1

    @property
    def memory_items(self) -> tuple[MemoryItem, ...]:
        return tuple(self._items.values())

    @property
    def candidate_reviews(self) -> tuple[CandidateReview, ...]:
        return tuple(self._reviews)

    def present_decision_material(
        self,
        event: SensoryEvent,
    ) -> MemoryDecisionMaterial:
        base = super().present_decision_material(event)
        return MemoryDecisionMaterial(
            lifecycle_id=event.correlation_id,
            version=f"memory:{self._material_version}",
            available=base.available,
            related_references=tuple(
                item.memory_item_id for item in self._items.values() if item.active
            ),
            confirmed_experience_references=tuple(
                item.memory_item_id
                for item in self._items.values()
                if item.active
                and item.source_kind is RetentionCandidateKind.EXPERIENCE
                and item.certainty is InformationCertainty.CONFIRMED
            ),
            uncertainties=base.uncertainties,
        )

    def review_candidate(self, candidate: RetentionCandidate) -> CandidateReview:
        review_id = self._required_unique_id(
            self._review_id_factory,
            self._review_ids,
            "candidate review",
        )
        previous_version = self._latest_candidate_versions.get(candidate.candidate_id)
        review_kind = (
            CandidateReviewKind.REEVALUATION
            if previous_version is not None
            else CandidateReviewKind.INITIAL
        )

        if candidate.proposed_owner is not ResponsibilityId.MEMORY:
            return self._record_review(
                CandidateReview(
                    review_id=review_id,
                    candidate_id=candidate.candidate_id,
                    candidate_version=candidate.version,
                    owner=ResponsibilityId.MEMORY,
                    kind=review_kind,
                    disposition=CandidateDisposition.REJECTED,
                    reason="candidate is addressed to another information owner",
                    reviewed_at=candidate.created_at,
                )
            )
        if previous_version is not None and candidate.version <= previous_version:
            return self._record_review(
                CandidateReview(
                    review_id=review_id,
                    candidate_id=candidate.candidate_id,
                    candidate_version=candidate.version,
                    owner=ResponsibilityId.MEMORY,
                    kind=review_kind,
                    disposition=CandidateDisposition.REJECTED,
                    reason="candidate version is stale or duplicated",
                    reviewed_at=candidate.created_at,
                )
            )

        self._latest_candidate_versions[candidate.candidate_id] = candidate.version
        self._candidates[candidate.candidate_id] = candidate
        if (
            candidate.kind is RetentionCandidateKind.EXPERIENCE
            and candidate.certainty is not InformationCertainty.CONFIRMED
        ):
            return self._record_review(
                CandidateReview(
                    review_id=review_id,
                    candidate_id=candidate.candidate_id,
                    candidate_version=candidate.version,
                    owner=ResponsibilityId.MEMORY,
                    kind=review_kind,
                    disposition=CandidateDisposition.HELD,
                    reason=(
                        "experience candidate has no confirmed later outcome; "
                        "it remains a candidate"
                    ),
                    reviewed_at=candidate.created_at,
                )
            )

        matching = next(
            (
                item
                for item in self._items.values()
                if item.active
                and item.source_kind is candidate.kind
                and item.meaning == candidate.meaning
            ),
            None,
        )
        if matching is not None:
            updated = replace(
                matching,
                version=matching.version + 1,
                source_candidate_ids=self._unique(
                    (*matching.source_candidate_ids, candidate.candidate_id)
                ),
                provenance_entry_ids=self._unique(
                    (*matching.provenance_entry_ids, *candidate.provenance_entry_ids)
                ),
                certainty=candidate.certainty,
                updated_at=candidate.created_at,
                update_reason=(
                    f"integrated candidate {candidate.candidate_id}: "
                    f"{candidate.reason}"
                ),
            )
            self._items[matching.memory_item_id] = updated
            self._material_version += 1
            return self._record_review(
                CandidateReview(
                    review_id=review_id,
                    candidate_id=candidate.candidate_id,
                    candidate_version=candidate.version,
                    owner=ResponsibilityId.MEMORY,
                    kind=review_kind,
                    disposition=CandidateDisposition.INTEGRATED,
                    reason="candidate meaning was integrated into existing memory",
                    reviewed_at=candidate.created_at,
                    memory_item_id=matching.memory_item_id,
                )
            )

        item_id = self._required_unique_id(
            self._memory_item_id_factory,
            self._memory_item_ids,
            "memory item",
        )
        self._items[item_id] = MemoryItem(
            memory_item_id=item_id,
            version=1,
            source_kind=candidate.kind,
            source_candidate_ids=(candidate.candidate_id,),
            provenance_entry_ids=candidate.provenance_entry_ids,
            meaning=candidate.meaning,
            certainty=candidate.certainty,
            created_at=candidate.created_at,
            updated_at=candidate.created_at,
            update_reason=(
                f"adopted candidate {candidate.candidate_id}: {candidate.reason}"
            ),
        )
        self._material_version += 1
        return self._record_review(
            CandidateReview(
                review_id=review_id,
                candidate_id=candidate.candidate_id,
                candidate_version=candidate.version,
                owner=ResponsibilityId.MEMORY,
                kind=review_kind,
                disposition=CandidateDisposition.ADOPTED,
                reason="candidate was adopted into Memory's owned information",
                reviewed_at=candidate.created_at,
                memory_item_id=item_id,
            )
        )

    def _record_review(self, review: CandidateReview) -> CandidateReview:
        self._reviews.append(review)
        return review

    @staticmethod
    def _required_unique_id(
        factory: Callable[[], str],
        used: set[str],
        label: str,
    ) -> str:
        value = factory()
        if not value.strip() or value in used:
            raise ValueError(f"{label} ID must be non-empty and unique")
        used.add(value)
        return value

    @staticmethod
    def _unique(values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(values))
