"""Memory-owned conversation memory and bounded deterministic retrieval."""

from __future__ import annotations

import unicodedata
from collections.abc import Callable
from dataclasses import replace
from uuid import uuid4

from yamicha.contracts import (
    CandidateDisposition,
    ConversationMemoryCandidate,
    ConversationMemoryForgetRecord,
    ConversationMemoryForgetRequest,
    ConversationMemoryItem,
    ConversationMemoryReview,
    ConversationMemorySelection,
    ConversationMemoryWindow,
    InformationCertainty,
    MemoryDecisionMaterial,
    MemoryPersistenceSnapshot,
    SensoryEvent,
)
from yamicha.life.stage7 import Stage7Memory


class Stage13Memory(Stage7Memory):
    _RECALL_MARKERS = (
        "覚えてる",
        "覚えていますか",
        "何を覚えて",
        "前に話した",
        "以前話した",
        "私について",
    )
    _SEARCH_IGNORED = (
        "覚えておいて",
        "覚えてる",
        "覚えていますか",
        "何を覚えて",
        "前に話した",
        "以前話した",
        "私について",
        "ください",
        "忘れて",
        "ことを",
        "のこと",
    )
    _BARE_FORGET_REQUESTS = (
        "忘れて",
        "それを忘れて",
        "それ忘れて",
        "これを忘れて",
        "これ忘れて",
    )

    def __init__(
        self,
        *,
        conversation_review_id_factory: Callable[[], str] | None = None,
        conversation_memory_item_id_factory: Callable[[], str] | None = None,
        conversation_forget_record_id_factory: Callable[[], str] | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self._conversation_review_id_factory = (
            conversation_review_id_factory or (lambda: str(uuid4()))
        )
        self._conversation_memory_item_id_factory = (
            conversation_memory_item_id_factory or (lambda: str(uuid4()))
        )
        self._conversation_forget_record_id_factory = (
            conversation_forget_record_id_factory or (lambda: str(uuid4()))
        )
        self._conversation_candidates: dict[str, ConversationMemoryCandidate] = {}
        self._conversation_candidate_versions: dict[str, int] = {}
        self._conversation_reviews: list[ConversationMemoryReview] = []
        self._conversation_items: dict[str, ConversationMemoryItem] = {}
        self._conversation_forget_records: list[ConversationMemoryForgetRecord] = []
        self._conversation_review_ids: set[str] = set()
        self._conversation_item_ids: set[str] = set()
        self._conversation_forget_record_ids: set[str] = set()
        self._prepared_window: tuple[str, ConversationMemoryWindow] | None = None

    @property
    def conversation_memory_items(self) -> tuple[ConversationMemoryItem, ...]:
        return tuple(self._conversation_items.values())

    @property
    def conversation_memory_reviews(self) -> tuple[ConversationMemoryReview, ...]:
        return tuple(self._conversation_reviews)

    @property
    def conversation_memory_forget_records(
        self,
    ) -> tuple[ConversationMemoryForgetRecord, ...]:
        return tuple(self._conversation_forget_records)

    def prepare_selection(
        self,
        event: SensoryEvent,
        *,
        max_items: int,
        max_characters: int,
    ) -> ConversationMemoryWindow | None:
        if max_items <= 0 or max_characters < 0:
            raise ValueError("conversation-memory selection limits are invalid")
        if not self._available:
            self._prepared_window = None
            return None
        query = self._normalized(event.meaning.normalized_text)
        bare_forget_requested = query in {
            self._normalized(value) for value in self._BARE_FORGET_REQUESTS
        }
        recall_requested = any(
            self._normalized(marker) in query for marker in self._RECALL_MARKERS
        )
        searchable_query = query
        for marker in self._SEARCH_IGNORED:
            searchable_query = searchable_query.replace(self._normalized(marker), "")
        targeted_literal = searchable_query.removesuffix(self._normalized("を"))
        query_fragments = self._fragments(searchable_query)
        ranked: list[tuple[int, ConversationMemoryItem, str]] = []
        for item in self._conversation_items.values():
            if not item.active or item.counterpart_id != event.source_id:
                continue
            shared = query_fragments & self._fragments(self._normalized(item.meaning))
            if bare_forget_requested:
                ranked.append(
                    (1, item, "bare forgetting request targets latest memory")
                )
            elif targeted_literal and targeted_literal in self._normalized(item.meaning):
                ranked.append(
                    (200, item, f"explicit target text: {targeted_literal}")
                )
            elif shared:
                fragments = "、".join(
                    sorted(shared, key=lambda value: (-len(value), value))[:5]
                )
                ranked.append(
                    (
                        100 + len(shared),
                        item,
                        f"shared text fragments: {fragments}",
                    )
                )
            elif recall_requested:
                ranked.append((1, item, "explicit recall request"))
        ranked.sort(
            key=lambda value: (
                -value[0],
                -value[1].updated_at.value.timestamp(),
                value[1].memory_item_id,
            )
        )
        remaining = max_characters
        selections: list[ConversationMemorySelection] = []
        for _, item, reason in ranked:
            if len(selections) >= max_items:
                break
            if bare_forget_requested and selections:
                break
            characters = len(item.meaning)
            if characters > remaining:
                continue
            selections.append(
                ConversationMemorySelection(
                    memory_item_id=item.memory_item_id,
                    memory_item_version=item.version,
                    counterpart_id=item.counterpart_id,
                    meaning=item.meaning,
                    source_references=item.source_references,
                    certainty=item.certainty,
                    content_trust=item.content_trust,
                    selection_reason=reason,
                    updated_at=item.updated_at,
                )
            )
            remaining -= characters
        window = ConversationMemoryWindow(
            memory_version=self._material_version,
            selections=tuple(selections),
            current_input_reference=event.event_id,
            max_items=max_items,
            max_characters=max_characters,
        )
        self._prepared_window = (event.correlation_id, window)
        return window

    def selected_window(self, lifecycle_id: str) -> ConversationMemoryWindow | None:
        if self._prepared_window is None or self._prepared_window[0] != lifecycle_id:
            return None
        return self._prepared_window[1]

    def present_decision_material(self, event: SensoryEvent) -> MemoryDecisionMaterial:
        base = super().present_decision_material(event)
        window = self.selected_window(event.correlation_id)
        references = () if window is None else tuple(
            selection.memory_item_id for selection in window.selections
        )
        return replace(
            base,
            related_references=references,
            conversation_memory_references=references,
        )

    def review_conversation_candidate(
        self,
        candidate: ConversationMemoryCandidate,
    ) -> ConversationMemoryReview:
        review_id = self._required_unique_id(
            self._conversation_review_id_factory,
            self._conversation_review_ids,
            "conversation-memory review",
        )
        previous_version = self._conversation_candidate_versions.get(
            candidate.candidate_id
        )
        if previous_version is not None and candidate.version <= previous_version:
            return self._record_conversation_review(
                ConversationMemoryReview(
                    review_id=review_id,
                    candidate_id=candidate.candidate_id,
                    candidate_version=candidate.version,
                    disposition=CandidateDisposition.REJECTED,
                    reason="candidate version is stale or duplicated",
                    reviewed_at=candidate.created_at,
                )
            )
        self._conversation_candidate_versions[candidate.candidate_id] = candidate.version
        self._conversation_candidates[candidate.candidate_id] = candidate
        if not self._available:
            return self._record_conversation_review(
                ConversationMemoryReview(
                    review_id=review_id,
                    candidate_id=candidate.candidate_id,
                    candidate_version=candidate.version,
                    disposition=CandidateDisposition.HELD,
                    reason="conversation memory is unavailable; candidate remains held",
                    reviewed_at=candidate.created_at,
                )
            )
        if candidate.certainty is InformationCertainty.UNKNOWN:
            return self._record_conversation_review(
                ConversationMemoryReview(
                    review_id=review_id,
                    candidate_id=candidate.candidate_id,
                    candidate_version=candidate.version,
                    disposition=CandidateDisposition.HELD,
                    reason="conversation memory with unknown certainty remains held",
                    reviewed_at=candidate.created_at,
                )
            )
        matching = next(
            (
                item
                for item in self._conversation_items.values()
                if item.active
                and item.counterpart_id == candidate.counterpart_id
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
                source_turn_ids=self._unique(
                    (*matching.source_turn_ids, *candidate.source_turn_ids)
                ),
                source_references=self._unique(
                    (*matching.source_references, *candidate.source_references)
                ),
                updated_at=candidate.created_at,
                retention_reason=candidate.retention_reason,
                certainty=(
                    InformationCertainty.CONFIRMED
                    if InformationCertainty.CONFIRMED
                    in (matching.certainty, candidate.certainty)
                    else InformationCertainty.INFERRED
                ),
            )
            self._conversation_items[matching.memory_item_id] = updated
            disposition = CandidateDisposition.INTEGRATED
            item_id = matching.memory_item_id
            reason = "candidate meaning was integrated into existing conversation memory"
        else:
            item_id = self._required_unique_id(
                self._conversation_memory_item_id_factory,
                self._conversation_item_ids,
                "conversation-memory item",
            )
            self._conversation_items[item_id] = ConversationMemoryItem(
                memory_item_id=item_id,
                counterpart_id=candidate.counterpart_id,
                version=1,
                meaning=candidate.meaning,
                source_candidate_ids=(candidate.candidate_id,),
                source_turn_ids=candidate.source_turn_ids,
                source_references=candidate.source_references,
                certainty=candidate.certainty,
                content_trust=candidate.content_trust,
                created_at=candidate.created_at,
                updated_at=candidate.created_at,
                retention_reason=candidate.retention_reason,
            )
            disposition = CandidateDisposition.ADOPTED
            reason = (
                "eligible counterpart information was adopted as conversation memory"
            )
        self._material_version += 1
        return self._record_conversation_review(
            ConversationMemoryReview(
                review_id=review_id,
                candidate_id=candidate.candidate_id,
                candidate_version=candidate.version,
                disposition=disposition,
                reason=reason,
                reviewed_at=candidate.created_at,
                memory_item_id=item_id,
            )
        )

    def forget_conversation_memory(
        self,
        request: ConversationMemoryForgetRequest,
    ) -> ConversationMemoryForgetRecord:
        if not self._available:
            raise RuntimeError("conversation memory is unavailable")
        targets: list[ConversationMemoryItem] = []
        for item_id, version in request.target_items:
            item = self._conversation_items.get(item_id)
            if (
                item is None
                or not item.active
                or item.counterpart_id != request.counterpart_id
                or item.version != version
                or request.requested_at.value < item.updated_at.value
            ):
                raise ValueError("conversation-memory forget target is stale or invalid")
            targets.append(item)
        deactivated_items: list[tuple[str, int, int]] = []
        for item in targets:
            updated = replace(
                item,
                version=item.version + 1,
                updated_at=request.requested_at,
                active=False,
                deactivated_at=request.requested_at,
                deactivation_reason=request.reason,
            )
            self._conversation_items[item.memory_item_id] = updated
            deactivated_items.append(
                (item.memory_item_id, item.version, updated.version)
            )
        record_id = self._required_unique_id(
            self._conversation_forget_record_id_factory,
            self._conversation_forget_record_ids,
            "conversation-memory forget record",
        )
        record = ConversationMemoryForgetRecord(
            record_id=record_id,
            request_id=request.request_id,
            lifecycle_id=request.lifecycle_id,
            counterpart_id=request.counterpart_id,
            deactivated_items=tuple(deactivated_items),
            source_reference=request.source_reference,
            reason=request.reason,
            occurred_at=request.requested_at,
        )
        self._conversation_forget_records.append(record)
        self._material_version += 1
        return record

    def persistence_snapshot(self) -> MemoryPersistenceSnapshot:
        base = super().persistence_snapshot()
        return replace(
            base,
            conversation_candidates=tuple(self._conversation_candidates.values()),
            conversation_reviews=tuple(self._conversation_reviews),
            conversation_items=tuple(self._conversation_items.values()),
            conversation_forget_records=tuple(self._conversation_forget_records),
        )

    def restore_owned_information(self, snapshot: MemoryPersistenceSnapshot) -> None:
        if (
            self._conversation_candidates
            or self._conversation_reviews
            or self._conversation_items
            or self._conversation_forget_records
        ):
            raise RuntimeError("Conversation Memory can only restore into a fresh owner")
        super().restore_owned_information(snapshot)
        self._conversation_candidates.update(
            (candidate.candidate_id, candidate)
            for candidate in snapshot.conversation_candidates
        )
        self._conversation_candidate_versions.update(
            (candidate.candidate_id, candidate.version)
            for candidate in snapshot.conversation_candidates
        )
        self._conversation_reviews.extend(snapshot.conversation_reviews)
        self._conversation_review_ids.update(
            review.review_id for review in snapshot.conversation_reviews
        )
        self._conversation_items.update(
            (item.memory_item_id, item) for item in snapshot.conversation_items
        )
        self._conversation_item_ids.update(self._conversation_items)
        self._conversation_forget_records.extend(snapshot.conversation_forget_records)
        self._conversation_forget_record_ids.update(
            record.record_id for record in snapshot.conversation_forget_records
        )

    def _record_conversation_review(
        self,
        review: ConversationMemoryReview,
    ) -> ConversationMemoryReview:
        self._conversation_reviews.append(review)
        return review

    @staticmethod
    def _normalized(value: str) -> str:
        normalized = unicodedata.normalize("NFKC", value).casefold()
        return "".join(character for character in normalized if character.isalnum())

    @staticmethod
    def _fragments(value: str) -> set[str]:
        if len(value) < 2:
            return set()
        return {
            value[index : index + size]
            for size in (2, 3)
            for index in range(len(value) - size + 1)
        }
