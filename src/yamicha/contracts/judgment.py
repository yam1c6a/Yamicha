"""Contracts for stage-4 judgment materials and finalization."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .input import ContentTrust, InputDisposition, InputRejection, SensoryEvent
from .lifecycle import DecisionDirection, OperatingState
from .requests import RoutedInputCycle


@dataclass(frozen=True, slots=True)
class StateDecisionMaterial:
    lifecycle_id: str
    version: str
    available: bool
    operating_state: OperatingState
    constraints: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MemoryDecisionMaterial:
    lifecycle_id: str
    version: str
    available: bool
    related_references: tuple[str, ...] = ()
    confirmed_experience_references: tuple[str, ...] = ()
    uncertainties: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RelationshipDecisionMaterial:
    lifecycle_id: str
    version: str
    available: bool
    counterpart_id: str
    counterpart_known: bool
    boundary_violation: bool
    boundary_reasons: tuple[str, ...] = ()
    current_consent: bool | None = None


@dataclass(frozen=True, slots=True)
class BoundaryDecisionMaterial:
    lifecycle_id: str
    version: str
    input_validated: bool
    content_trust: ContentTrust
    external_effects_permitted: bool


@dataclass(frozen=True, slots=True)
class JudgmentContext:
    """Versioned materials supplied to Judgment for one lifecycle."""

    lifecycle_id: str
    event: SensoryEvent
    state: StateDecisionMaterial
    memory: MemoryDecisionMaterial
    relationship: RelationshipDecisionMaterial
    boundary: BoundaryDecisionMaterial
    auxiliary_intelligence_available: bool = False

    def __post_init__(self) -> None:
        lifecycle_ids = {
            self.lifecycle_id,
            self.event.correlation_id,
            self.state.lifecycle_id,
            self.memory.lifecycle_id,
            self.relationship.lifecycle_id,
            self.boundary.lifecycle_id,
        }
        if len(lifecycle_ids) != 1 or not self.lifecycle_id.strip():
            raise ValueError("judgment materials must belong to one lifecycle")

    @property
    def material_versions(self) -> tuple[tuple[str, str], ...]:
        return (
            ("state", self.state.version),
            ("memory", self.memory.version),
            ("relationship", self.relationship.version),
            ("boundary", self.boundary.version),
        )


@dataclass(frozen=True, slots=True)
class DecisionCandidate:
    direction: DecisionDirection
    selected: bool
    acceptance_reasons: tuple[str, ...]
    rejection_reasons: tuple[str, ...]
    uncertainties: tuple[str, ...] = ()
    confirmation_conditions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.selected and not self.acceptance_reasons:
            raise ValueError("selected candidate requires an acceptance reason")
        if not self.selected and not self.rejection_reasons:
            raise ValueError("rejected candidate requires a rejection reason")


@dataclass(frozen=True, slots=True)
class JudgmentResult:
    lifecycle_id: str
    candidates: tuple[DecisionCandidate, ...]
    selected_direction: DecisionDirection
    uncertainties: tuple[str, ...]
    material_versions: tuple[tuple[str, str], ...]
    auxiliary_intelligence_used: bool = False

    def __post_init__(self) -> None:
        directions = tuple(candidate.direction for candidate in self.candidates)
        if len(directions) != len(DecisionDirection) or set(directions) != set(
            DecisionDirection
        ):
            raise ValueError("judgment must record every decision direction once")
        selected = tuple(
            candidate.direction for candidate in self.candidates if candidate.selected
        )
        if selected != (self.selected_direction,):
            raise ValueError("judgment must select exactly its declared direction")
        if len(dict(self.material_versions)) != 4:
            raise ValueError("judgment must retain all material versions")


class FinalizationStatus(StrEnum):
    FINALIZED = "finalized"
    REMANDED = "remanded"


@dataclass(frozen=True, slots=True)
class JudgmentFinalization:
    lifecycle_id: str
    status: FinalizationStatus
    proposed_direction: DecisionDirection
    finalized_direction: DecisionDirection | None
    reason: str

    def __post_init__(self) -> None:
        finalized = self.status is FinalizationStatus.FINALIZED
        if finalized != (self.finalized_direction is not None):
            raise ValueError("only finalized judgment has a finalized direction")
        if finalized and self.finalized_direction is not self.proposed_direction:
            raise ValueError("Core cannot replace Judgment's direction")
        if not self.reason.strip():
            raise ValueError("finalization requires a reason")


@dataclass(frozen=True, slots=True)
class Stage4InputOutcome:
    correlation_id: str
    disposition: InputDisposition
    rejection: InputRejection | None = None
    cycle: RoutedInputCycle | None = None
    context: JudgmentContext | None = None
    judgment: JudgmentResult | None = None
    finalization: JudgmentFinalization | None = None

    def __post_init__(self) -> None:
        accepted = self.disposition in {
            InputDisposition.ACCEPTED,
            InputDisposition.DUPLICATE,
        }
        if accepted != (self.cycle is not None):
            raise ValueError("accepted input requires a routed cycle")
        if accepted == (self.rejection is not None):
            raise ValueError("rejected input requires only a rejection")
        has_judgment = (
            self.context is not None
            and self.judgment is not None
            and self.finalization is not None
        )
        if has_judgment != (self.disposition is InputDisposition.ACCEPTED):
            raise ValueError("only newly accepted input proceeds to judgment")
