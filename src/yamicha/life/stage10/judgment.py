"""Judgment proposals and reviews for auxiliary-intelligence material."""

from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from yamicha.contracts import (
    AuxiliaryIntelligenceProposal,
    AuxiliaryIntelligenceResult,
    ExternalTime,
    IntelligenceCandidateReview,
    IntelligenceConstraints,
    IntelligencePurpose,
    IntelligenceResultStatus,
)
from yamicha.life.stage9 import Stage9Judgment


class Stage10Judgment(Stage9Judgment):
    def __init__(
        self,
        *,
        intelligence_proposal_id_factory: Callable[[], str] | None = None,
        intelligence_review_id_factory: Callable[[], str] | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self._intelligence_proposal_id_factory = (
            intelligence_proposal_id_factory or (lambda: str(uuid4()))
        )
        self._intelligence_review_id_factory = (
            intelligence_review_id_factory or (lambda: str(uuid4()))
        )
        self._intelligence_proposals: dict[str, AuxiliaryIntelligenceProposal] = {}
        self._intelligence_reviews: dict[str, IntelligenceCandidateReview] = {}

    def propose_dialogue_assistance(
        self,
        *,
        lifecycle_id: str,
        model: str,
        input_text: str,
        input_source_reference: str,
        constraints: IntelligenceConstraints,
        proposed_at: ExternalTime,
    ) -> AuxiliaryIntelligenceProposal:
        proposal = AuxiliaryIntelligenceProposal(
            proposal_id=self._required_id(
                self._intelligence_proposal_id_factory,
                "proposal",
            ),
            lifecycle_id=lifecycle_id,
            purpose=IntelligencePurpose.DIALOGUE_RESPONSE_CANDIDATE,
            model=model,
            input_text=input_text,
            input_source_reference=input_source_reference,
            constraints=constraints,
            proposed_at=proposed_at,
        )
        self._intelligence_proposals[proposal.proposal_id] = proposal
        return proposal

    def review_intelligence_result(
        self,
        result: AuxiliaryIntelligenceResult,
        reviewed_at: ExternalTime,
    ) -> IntelligenceCandidateReview:
        accepted = (
            result.status is IntelligenceResultStatus.SUCCESS
            and result.candidate is not None
            and result.candidate.unverified
        )
        review = IntelligenceCandidateReview(
            review_id=self._required_id(
                self._intelligence_review_id_factory,
                "review",
            ),
            request_id=result.request_id,
            result_id=result.result_id,
            candidate_id=(
                result.candidate.candidate_id if accepted and result.candidate else None
            ),
            accepted=accepted,
            reason=(
                "candidate is structurally usable as unverified material"
                if accepted
                else f"candidate is unavailable: {result.status.value}"
            ),
            reviewed_at=reviewed_at,
        )
        self._intelligence_reviews[review.review_id] = review
        return review

    def issued_intelligence_proposal(
        self,
        proposal: AuxiliaryIntelligenceProposal,
    ) -> bool:
        return self._intelligence_proposals.get(proposal.proposal_id) == proposal

    def issued_intelligence_review(
        self,
        review: IntelligenceCandidateReview,
    ) -> bool:
        return self._intelligence_reviews.get(review.review_id) == review

    @staticmethod
    def _required_id(factory: Callable[[], str], label: str) -> str:
        value = factory()
        if not value.strip():
            raise ValueError(f"intelligence {label} ID must not be empty")
        return value
