"""Core authorization, adoption, and expression review for stage 10."""

from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from yamicha.contracts import (
    AuxiliaryIntelligenceProposal,
    AuxiliaryIntelligenceResult,
    DecisionDirection,
    ExpressionArtifact,
    ExpressionRequest,
    ExpressionReview,
    ExpressionReviewStatus,
    ExternalTime,
    FinalizationStatus,
    IntegratedIntelligenceRequest,
    IntelligenceAdoption,
    IntelligenceAdoptionStatus,
    IntelligenceCandidateReview,
    JudgmentFinalization,
)
from yamicha.life.stage9 import Stage9Core


class Stage10Core(Stage9Core):
    def __init__(
        self,
        *,
        intelligence_request_id_factory: Callable[[], str] | None = None,
        intelligence_authorization_id_factory: Callable[[], str] | None = None,
        intelligence_adoption_id_factory: Callable[[], str] | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self._intelligence_request_id_factory = (
            intelligence_request_id_factory or (lambda: str(uuid4()))
        )
        self._intelligence_authorization_id_factory = (
            intelligence_authorization_id_factory or (lambda: str(uuid4()))
        )
        self._intelligence_adoption_id_factory = (
            intelligence_adoption_id_factory or (lambda: str(uuid4()))
        )
        self._proposal_validator: Callable[[AuxiliaryIntelligenceProposal], bool] | None = None
        self._result_validator: Callable[[AuxiliaryIntelligenceResult], bool] | None = None
        self._review_validator: Callable[[IntelligenceCandidateReview], bool] | None = None
        self._intelligence_requests: dict[str, IntegratedIntelligenceRequest] = {}
        self._intelligence_adoptions: dict[str, IntelligenceAdoption] = {}

    def bind_intelligence_validators(
        self,
        *,
        proposal_validator: Callable[[AuxiliaryIntelligenceProposal], bool],
        result_validator: Callable[[AuxiliaryIntelligenceResult], bool],
        review_validator: Callable[[IntelligenceCandidateReview], bool],
    ) -> None:
        if self._proposal_validator is not None:
            raise RuntimeError("intelligence validators are already bound")
        self._proposal_validator = proposal_validator
        self._result_validator = result_validator
        self._review_validator = review_validator

    def integrate_intelligence_request(
        self,
        proposal: AuxiliaryIntelligenceProposal,
        finalization: JudgmentFinalization,
        integrated_at: ExternalTime,
    ) -> IntegratedIntelligenceRequest:
        if self._proposal_validator is None or not self._proposal_validator(proposal):
            raise ValueError("intelligence proposal was not issued by Judgment")
        if (
            finalization.status is not FinalizationStatus.FINALIZED
            or finalization.finalized_direction is not DecisionDirection.RESPOND
            or finalization.lifecycle_id != proposal.lifecycle_id
        ):
            raise ValueError("intelligence use requires a finalized response direction")
        request = IntegratedIntelligenceRequest(
            request_id=self._required_id(
                self._intelligence_request_id_factory,
                "request",
            ),
            proposal=proposal,
            core_authorization_id=self._required_id(
                self._intelligence_authorization_id_factory,
                "authorization",
            ),
            integrated_at=integrated_at,
        )
        self._intelligence_requests[request.request_id] = request
        return request

    def issued_intelligence_request(
        self,
        request: IntegratedIntelligenceRequest,
    ) -> bool:
        return self._intelligence_requests.get(request.request_id) == request

    def finalize_intelligence_adoption(
        self,
        *,
        request: IntegratedIntelligenceRequest,
        result: AuxiliaryIntelligenceResult,
        review: IntelligenceCandidateReview,
        finalization: JudgmentFinalization,
        decided_at: ExternalTime,
    ) -> IntelligenceAdoption:
        if not self.issued_intelligence_request(request):
            raise ValueError("intelligence request was not issued by Core")
        if self._result_validator is None or not self._result_validator(result):
            raise ValueError("intelligence result was not issued by AuxiliaryIntelligence")
        if self._review_validator is None or not self._review_validator(review):
            raise ValueError("intelligence review was not issued by Judgment")
        if (
            result.request_id != request.request_id
            or review.request_id != request.request_id
            or review.result_id != result.result_id
            or finalization.lifecycle_id != request.proposal.lifecycle_id
            or finalization.finalized_direction is not DecisionDirection.RESPOND
        ):
            raise ValueError("intelligence adoption inputs do not identify one lifecycle")
        adopted = (
            review.accepted
            and result.candidate is not None
            and review.candidate_id == result.candidate.candidate_id
            and self._candidate_preserves_verified_identity(
                request,
                result.candidate.text,
            )
        )
        adoption = IntelligenceAdoption(
            adoption_id=self._required_id(
                self._intelligence_adoption_id_factory,
                "adoption",
            ),
            lifecycle_id=request.proposal.lifecycle_id,
            request_id=request.request_id,
            result_id=result.result_id,
            review_id=review.review_id,
            candidate_id=(result.candidate.candidate_id if adopted and result.candidate else None),
            status=(
                IntelligenceAdoptionStatus.ADOPTED
                if adopted
                else IntelligenceAdoptionStatus.REJECTED
            ),
            reason=(
                "Core adopted the bounded candidate for this finalized response"
                if adopted
                else (
                    "Core rejected a candidate that changed verified identity"
                    if result.candidate is not None
                    and not self._candidate_preserves_verified_identity(
                        request,
                        result.candidate.text,
                    )
                    else "Core retained the deterministic fallback response"
                )
            ),
            decided_at=decided_at,
        )
        self._intelligence_adoptions[adoption.adoption_id] = adoption
        return adoption

    def issued_intelligence_adoption(self, adoption: IntelligenceAdoption) -> bool:
        return self._intelligence_adoptions.get(adoption.adoption_id) == adoption

    @classmethod
    def _candidate_preserves_verified_identity(
        cls,
        request: IntegratedIntelligenceRequest,
        candidate_text: str,
    ) -> bool:
        constraints = request.proposal.constraints
        normalized_candidate = cls._normalize_identity_text(candidate_text)
        if any(
            cls._normalize_identity_text(fragment) in normalized_candidate
            for fragment in constraints.forbidden_self_identification
        ):
            return False
        normalized_input = cls._normalize_identity_text(
            request.proposal.input_text
        )
        identity_questions = (
            "あなたは誰",
            "あなたは何者",
            "君は誰",
            "名前は",
            "お名前",
            "whoareyou",
            "whatareyou",
            "yourname",
        )
        identity_shorthand = {
            "あなたは?",
            "あなたは？",
            "君は?",
            "君は？",
            "きみは?",
            "きみは？",
        }
        if (
            normalized_input in identity_shorthand
            or any(question in normalized_input for question in identity_questions)
        ):
            if constraints.speaker_name.casefold() not in candidate_text.casefold():
                return False
        model_question = (
            normalized_input
            in {"モデルは?", "モデルは？", "何のモデル?", "何のモデル？"}
            or (
                "モデル" in normalized_input
                and any(
                    subject in normalized_input
                    for subject in ("補助知能", "llm", "ai")
                )
            )
        )
        if (
            model_question
            and request.proposal.model.casefold()
            not in candidate_text.casefold()
        ):
            return False
        return True

    @staticmethod
    def _normalize_identity_text(value: str) -> str:
        return "".join(value.casefold().replace("’", "'").split())

    def review_intelligence_expression(
        self,
        request: ExpressionRequest,
        artifact: ExpressionArtifact,
        adoption: IntelligenceAdoption,
        result: AuxiliaryIntelligenceResult,
    ) -> ExpressionReview:
        candidate = result.candidate
        accepted = (
            self.issued_intelligence_adoption(adoption)
            and adoption.status is IntelligenceAdoptionStatus.ADOPTED
            and candidate is not None
            and adoption.candidate_id == candidate.candidate_id
            and artifact.external_intelligence_used
            and artifact.lifecycle_id == request.lifecycle_id
            and artifact.request_id == request.request_id
            and artifact.direction is request.direction
            and artifact.mode is request.mode
            and tuple(statement.item for statement in artifact.statements) == request.items
            and artifact.text == candidate.text
            and not artifact.claimed_completed_effects
        )
        return ExpressionReview(
            lifecycle_id=request.lifecycle_id,
            request_id=request.request_id,
            artifact_id=artifact.artifact_id,
            direction=request.direction,
            status=(
                ExpressionReviewStatus.ACCEPTED
                if accepted
                else ExpressionReviewStatus.REEXPRESSION_REQUIRED
            ),
            reason=(
                "expression exactly preserves the Core-adopted intelligence candidate"
                if accepted
                else "expression does not match the Core-adopted intelligence candidate"
            ),
        )

    @staticmethod
    def _required_id(factory: Callable[[], str], label: str) -> str:
        value = factory()
        if not value.strip():
            raise ValueError(f"intelligence {label} ID must not be empty")
        return value
