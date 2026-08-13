"""Core finalization of the same stage-8 protection release evaluation."""

from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from yamicha.contracts import (
    ExternalTime,
    ProtectionReleaseEvaluation,
    ProtectionReleaseProposal,
)
from yamicha.life.stage7 import (
    Stage7Core,
    Stage7Memory,
    Stage7Relationship,
    Stage7State,
)


class Stage8Core(Stage7Core):
    def __init__(
        self,
        *,
        release_finalization_id_factory: Callable[[], str] | None = None,
        release_proposal_id_factory: Callable[[], str] | None = None,
        state: Stage7State,
        memory: Stage7Memory,
        relationship: Stage7Relationship,
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
            lifecycle_record_id_factory=lifecycle_record_id_factory,
            record_entry_id_factory=record_entry_id_factory,
        )
        self._release_finalization_id_factory = (
            release_finalization_id_factory or (lambda: str(uuid4()))
        )
        self._release_proposal_id_factory = (
            release_proposal_id_factory or (lambda: str(uuid4()))
        )
        self._release_proposals: dict[str, ProtectionReleaseProposal] = {}

    def finalize_protection_release(
        self,
        evaluation: ProtectionReleaseEvaluation,
        finalized_at: ExternalTime,
    ) -> ProtectionReleaseProposal:
        proposal = ProtectionReleaseProposal(
            proposal_id=self._release_proposal_id_factory(),
            activation_id=evaluation.activation_id,
            protection_definition_version=(
                evaluation.protection_definition_version
            ),
            judgment_approval_id=evaluation.evaluation_id,
            core_finalization_id=self._release_finalization_id_factory(),
            created_at=finalized_at,
        )
        self._release_proposals[proposal.proposal_id] = proposal
        return proposal

    def issued_release_proposal(
        self,
        proposal_id: str,
    ) -> ProtectionReleaseProposal | None:
        return self._release_proposals.get(proposal_id)
