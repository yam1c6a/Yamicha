"""Contracts for stage-2 execution opportunities and no-action cycles."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .time import ElapsedTime, ExternalTime, InternalTime, MonotonicTime


class ExecutionOpportunityKind(StrEnum):
    STARTUP = "startup"
    PERIODIC = "periodic"


@dataclass(frozen=True, slots=True)
class ExecutionOpportunity:
    """A body-provided chance to run, never a desire or decision reason."""

    opportunity_id: str
    sequence: int
    kind: ExecutionOpportunityKind
    external_time: ExternalTime
    monotonic_time: MonotonicTime
    elapsed_since_previous: ElapsedTime

    def __post_init__(self) -> None:
        if not self.opportunity_id.strip():
            raise ValueError("opportunity_id must not be empty")
        if self.sequence < 1:
            raise ValueError("execution opportunity sequence must be positive")


@dataclass(frozen=True, slots=True)
class InternalEvent:
    """A normalized internal event received by Sensation."""

    correlation_id: str
    source: str
    opportunity: ExecutionOpportunity

    def __post_init__(self) -> None:
        if not self.correlation_id.strip() or not self.source.strip():
            raise ValueError("internal event identifiers must not be empty")


class OperatingState(StrEnum):
    NOT_STARTED = "not_started"
    ACTIVE = "active"
    WAITING = "waiting"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class StateSnapshot:
    """Current state evidence owned and presented by State."""

    correlation_id: str
    operating_state: OperatingState
    internal_time: InternalTime
    observed_external_time: ExternalTime
    pending_items: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()


class DecisionDirection(StrEnum):
    NO_ACTION = "no_action"


class DecisionBasis(StrEnum):
    STATE_SNAPSHOT = "state_snapshot"


@dataclass(frozen=True, slots=True)
class DecisionProposal:
    """A direction proposed by Judgment, not yet Yamicha's final decision."""

    correlation_id: str
    direction: DecisionDirection
    basis: DecisionBasis
    reason: str

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError("decision proposal reason must not be empty")


@dataclass(frozen=True, slots=True)
class FinalizedDecision:
    """The same proposed direction after Core's consistency check."""

    correlation_id: str
    direction: DecisionDirection
    basis: DecisionBasis
    reason: str


class CycleStatus(StrEnum):
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class CycleOutcome:
    """Traceable result of one input-free stage-2 lifecycle."""

    status: CycleStatus
    opportunity: ExecutionOpportunity
    observed_state: StateSnapshot
    proposal: DecisionProposal
    decision: FinalizedDecision
    final_state: StateSnapshot
    external_effect_count: int = 0
    memory_update_count: int = 0
