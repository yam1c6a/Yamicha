"""Judgment-owned proposal for the first bounded capability."""

from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from yamicha.contracts import (
    CapabilityOperation,
    CapabilityUseProposal,
    ExternalTime,
)
from yamicha.life.stage8 import Stage8Judgment


class Stage9Judgment(Stage8Judgment):
    def __init__(
        self,
        *,
        capability_proposal_id_factory: Callable[[], str] | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self._capability_proposal_id_factory = (
            capability_proposal_id_factory or (lambda: str(uuid4()))
        )
        self._capability_proposals: dict[str, CapabilityUseProposal] = {}

    def propose_read_capability(
        self,
        *,
        target: str,
        authority_id: str,
        expected_effect: str,
        idempotency_key: str,
        reason: str,
        proposed_at: ExternalTime,
    ) -> CapabilityUseProposal:
        proposal = CapabilityUseProposal(
            proposal_id=self._capability_proposal_id_factory(),
            target=target,
            operation=CapabilityOperation.READ_TEXT,
            authority_id=authority_id,
            expected_effect=expected_effect,
            idempotency_key=idempotency_key,
            reason=reason,
            verification_required=True,
            proposed_at=proposed_at,
        )
        self._capability_proposals[proposal.proposal_id] = proposal
        return proposal

    def issued_capability_proposal(
        self,
        proposal_id: str,
    ) -> CapabilityUseProposal | None:
        return self._capability_proposals.get(proposal_id)
