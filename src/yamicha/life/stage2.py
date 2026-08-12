"""Minimal organ behavior needed for an input-free stage-2 lifecycle."""

from __future__ import annotations

from yamicha.contracts import (
    DecisionBasis,
    DecisionDirection,
    DecisionProposal,
    ExecutionOpportunity,
    ExternalTime,
    FinalizedDecision,
    InternalEvent,
    InternalTime,
    MessageEnvelope,
    OperatingState,
    StateSnapshot,
    UnimplementedResponsibilityError,
)

from .ports import (
    CORE_DEFINITION,
    JUDGMENT_DEFINITION,
    SENSATION_DEFINITION,
    STATE_DEFINITION,
)


class Stage2Sensation:
    definition = SENSATION_DEFINITION

    def receive_execution_opportunity(
        self,
        opportunity: ExecutionOpportunity,
        correlation_id: str,
    ) -> InternalEvent:
        return InternalEvent(
            correlation_id=correlation_id,
            source="runtime",
            opportunity=opportunity,
        )

    def handle(self, message: MessageEnvelope) -> MessageEnvelope:
        raise UnimplementedResponsibilityError(
            "external sensation input starts after stage 2"
        )


class Stage2State:
    definition = STATE_DEFINITION

    def __init__(self) -> None:
        self._operating_state = OperatingState.NOT_STARTED
        self._internal_time: InternalTime | None = None
        self._last_correlation_id: str | None = None

    @property
    def operating_state(self) -> OperatingState:
        return self._operating_state

    def observe_internal_event(self, event: InternalEvent) -> StateSnapshot:
        opportunity = event.opportunity
        if self._operating_state is OperatingState.NOT_STARTED:
            if opportunity.sequence != 1:
                raise ValueError("the first state observation must be sequence 1")
            internal_time = InternalTime.initial(opportunity.external_time)
        elif self._operating_state is OperatingState.WAITING:
            if self._internal_time is None:
                raise RuntimeError("waiting state has no internal time")
            internal_time = self._internal_time.advance(
                opportunity.elapsed_since_previous,
                opportunity.external_time,
            )
        else:
            raise RuntimeError(
                f"state cannot observe an opportunity from {self._operating_state}"
            )
        self._internal_time = internal_time
        self._last_correlation_id = event.correlation_id
        self._operating_state = OperatingState.ACTIVE
        return self._snapshot(event.correlation_id, opportunity.external_time)

    def enter_waiting(self, decision: FinalizedDecision) -> StateSnapshot:
        if self._operating_state is not OperatingState.ACTIVE:
            raise RuntimeError(f"state cannot wait from {self._operating_state}")
        if decision.direction is not DecisionDirection.NO_ACTION:
            raise ValueError("stage-2 waiting requires a finalized no-action decision")
        if decision.correlation_id != self._last_correlation_id:
            raise ValueError("decision does not belong to the active lifecycle")
        self._operating_state = OperatingState.WAITING
        if self._internal_time is None:
            raise RuntimeError("active state has no internal time")
        return self._snapshot(
            decision.correlation_id,
            self._internal_time.updated_at,
        )

    def stop(self) -> None:
        if self._operating_state not in {
            OperatingState.NOT_STARTED,
            OperatingState.ACTIVE,
            OperatingState.WAITING,
        }:
            raise RuntimeError(f"state cannot stop from {self._operating_state}")
        self._operating_state = OperatingState.STOPPED

    def handle(self, message: MessageEnvelope) -> MessageEnvelope:
        raise UnimplementedResponsibilityError(
            "generic state message handling starts after stage 2"
        )

    def _snapshot(
        self,
        correlation_id: str,
        observed_external_time: ExternalTime,
    ) -> StateSnapshot:
        if self._internal_time is None:
            raise RuntimeError("state has no internal time")
        return StateSnapshot(
            correlation_id=correlation_id,
            operating_state=self._operating_state,
            internal_time=self._internal_time,
            observed_external_time=observed_external_time,
            pending_items=(),
            constraints=(),
        )


class Stage2Judgment:
    definition = JUDGMENT_DEFINITION

    def propose_for_state(self, snapshot: StateSnapshot) -> DecisionProposal:
        if snapshot.operating_state is not OperatingState.ACTIVE:
            raise ValueError("judgment requires an active state snapshot")
        if snapshot.pending_items or snapshot.constraints:
            raise UnimplementedResponsibilityError(
                "non-empty state evaluation starts after stage 2"
            )
        return DecisionProposal(
            correlation_id=snapshot.correlation_id,
            direction=DecisionDirection.NO_ACTION,
            basis=DecisionBasis.STATE_SNAPSHOT,
            reason=(
                "state snapshot reports no pending matter and no constraint "
                "requiring action"
            ),
        )

    def handle(self, message: MessageEnvelope) -> MessageEnvelope:
        raise UnimplementedResponsibilityError(
            "generic judgment message handling starts after stage 2"
        )


class Stage2Core:
    definition = CORE_DEFINITION

    def finalize(self, proposal: DecisionProposal) -> FinalizedDecision:
        if proposal.direction is not DecisionDirection.NO_ACTION:
            raise UnimplementedResponsibilityError(
                "directions other than no-action start after stage 2"
            )
        if proposal.basis is not DecisionBasis.STATE_SNAPSHOT:
            raise ValueError("stage-2 decision must be based on a state snapshot")
        return FinalizedDecision(
            correlation_id=proposal.correlation_id,
            direction=proposal.direction,
            basis=proposal.basis,
            reason=proposal.reason,
        )

    def handle(self, message: MessageEnvelope) -> MessageEnvelope:
        raise UnimplementedResponsibilityError(
            "generic core message routing starts after stage 2"
        )
