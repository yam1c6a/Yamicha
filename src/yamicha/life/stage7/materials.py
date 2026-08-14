"""Owner-controlled export and restoration for stage-7 information."""

from __future__ import annotations

from dataclasses import replace

from yamicha.contracts import (
    CandidateDisposition,
    ElapsedTime,
    ExecutionOpportunityKind,
    InternalEvent,
    MemoryPersistenceSnapshot,
    ProtectionPersistenceSnapshot,
    RelationshipPersistenceSnapshot,
    ResponsibilityId,
    StateSnapshot,
    StatePersistenceSnapshot,
)
from yamicha.life.stage4 import Stage4Relationship, Stage4State
from yamicha.life.stage6 import Stage6Memory


class Stage7State(Stage4State):
    def __init__(self) -> None:
        super().__init__()
        self._restored_for_restart = False

    def observe_internal_event(self, event: InternalEvent) -> StateSnapshot:
        if not self._restored_for_restart:
            return super().observe_internal_event(event)
        opportunity = event.opportunity
        if (
            opportunity.kind is not ExecutionOpportunityKind.STARTUP
            or opportunity.sequence != 1
        ):
            raise ValueError(
                "restored state requires the first startup execution opportunity"
            )
        if self._internal_time is None:
            raise RuntimeError("restored state has no internal time")
        stopped_elapsed = (
            opportunity.external_time.value - self._internal_time.updated_at.value
        )
        if stopped_elapsed.total_seconds() < 0:
            raise ValueError("restart time precedes the last persisted state time")
        resumed_event = replace(
            event,
            opportunity=replace(
                opportunity,
                elapsed_since_previous=ElapsedTime(stopped_elapsed),
            ),
        )
        snapshot = super().observe_internal_event(resumed_event)
        self._restored_for_restart = False
        return snapshot

    def persistence_snapshot(self) -> StatePersistenceSnapshot:
        if self._internal_time is None or self._last_correlation_id is None:
            raise RuntimeError("State has no completed cycle to persist")
        return StatePersistenceSnapshot(
            operating_state=self._operating_state,
            internal_time=self._internal_time,
            last_correlation_id=self._last_correlation_id,
            material_version=self._material_version,
        )

    def restore_owned_state(self, snapshot: StatePersistenceSnapshot) -> None:
        if (
            self._internal_time is not None
            or self._last_correlation_id is not None
            or self._material_version != 0
        ):
            raise RuntimeError("State can only be restored into a fresh owner")
        self._operating_state = snapshot.operating_state
        self._internal_time = snapshot.internal_time
        self._last_correlation_id = snapshot.last_correlation_id
        self._material_version = snapshot.material_version
        self._restored_for_restart = True


class Stage7Memory(Stage6Memory):
    def persistence_snapshot(self) -> MemoryPersistenceSnapshot:
        return MemoryPersistenceSnapshot(
            available=self._available,
            material_version=self._material_version,
            candidates=tuple(self._candidates.values()),
            candidate_versions=tuple(sorted(self._latest_candidate_versions.items())),
            reviews=tuple(self._reviews),
            items=tuple(self._items.values()),
        )

    def restore_owned_information(
        self,
        snapshot: MemoryPersistenceSnapshot,
    ) -> None:
        if (
            self._candidates
            or self._reviews
            or self._items
            or self._latest_candidate_versions
        ):
            raise RuntimeError("Memory can only be restored into a fresh owner")
        if any(
            candidate.proposed_owner is not ResponsibilityId.MEMORY
            for candidate in snapshot.candidates
        ):
            raise ValueError("persisted candidate is owned by another organ")
        candidate_ids = {candidate.candidate_id for candidate in snapshot.candidates}
        item_ids = {item.memory_item_id for item in snapshot.items}
        if any(
            not set(item.source_candidate_ids).issubset(candidate_ids)
            for item in snapshot.items
        ):
            raise ValueError("persisted memory item has unknown candidate provenance")
        if any(
            review.disposition
            in {CandidateDisposition.ADOPTED, CandidateDisposition.INTEGRATED}
            and review.memory_item_id not in item_ids
            for review in snapshot.reviews
        ):
            raise ValueError("persisted review references an unknown memory item")
        self._available = snapshot.available
        self._material_version = snapshot.material_version
        self._latest_candidate_versions.update(snapshot.candidate_versions)
        self._candidates.update(
            (candidate.candidate_id, candidate) for candidate in snapshot.candidates
        )
        self._reviews.extend(snapshot.reviews)
        self._items.update((item.memory_item_id, item) for item in snapshot.items)
        self._review_ids.update(review.review_id for review in snapshot.reviews)
        self._memory_item_ids.update(item_ids)


class Stage7Relationship(Stage4Relationship):
    def persistence_snapshot(self) -> RelationshipPersistenceSnapshot:
        return RelationshipPersistenceSnapshot(
            known_counterpart_id=self._known_counterpart_id,
        )

    def restore_owned_state(
        self,
        snapshot: RelationshipPersistenceSnapshot,
    ) -> None:
        self._known_counterpart_id = snapshot.known_counterpart_id
