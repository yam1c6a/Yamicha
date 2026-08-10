"""Construct all stage-1 responsibilities from one composition root."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from yamicha.body.external_effect_gate import (
    ExternalEffectGatePort,
    ExternalEffectGateStub,
)
from yamicha.body.protection_boundary import (
    ProtectionBoundaryPort,
    ProtectionBoundaryStub,
)
from yamicha.body.runtime import RuntimePort, RuntimeStub
from yamicha.contracts import (
    ResponsibilityCategory,
    ResponsibilityId,
    ResponsibilityPort,
)
from yamicha.life.ports import (
    AuxiliaryIntelligencePort,
    CapabilityPort,
    CorePort,
    JudgmentPort,
    LanguagePort,
    MemoryPort,
    RelationshipPort,
    SensationPort,
    StatePort,
)
from yamicha.life.stubs import (
    AuxiliaryIntelligenceStub,
    CapabilityStub,
    CoreStub,
    JudgmentStub,
    LanguageStub,
    MemoryStub,
    RelationshipStub,
    SensationStub,
    StateStub,
)


@dataclass(frozen=True, slots=True)
class YamichaComposition:
    """Complete responsibility set; the collective, not a component, is Yamicha."""

    subject_is_collective: ClassVar[bool] = True

    core: CorePort
    memory: MemoryPort
    state: StatePort
    sensation: SensationPort
    judgment: JudgmentPort
    relationship: RelationshipPort
    capability: CapabilityPort
    language: LanguagePort
    auxiliary_intelligence: AuxiliaryIntelligencePort
    runtime: RuntimePort
    protection_boundary: ProtectionBoundaryPort
    external_effect_gate: ExternalEffectGatePort

    @property
    def responsibilities(self) -> tuple[ResponsibilityPort, ...]:
        return (
            self.core,
            self.memory,
            self.state,
            self.sensation,
            self.judgment,
            self.relationship,
            self.capability,
            self.language,
            self.auxiliary_intelligence,
            self.runtime,
            self.protection_boundary,
            self.external_effect_gate,
        )

    def __post_init__(self) -> None:
        actual = tuple(port.definition.identifier for port in self.responsibilities)
        expected = tuple(ResponsibilityId)
        if len(actual) != len(set(actual)) or set(actual) != set(expected):
            raise ValueError(
                f"composition must contain every responsibility exactly once: {actual}"
            )
        organ_count = sum(
            port.definition.category is ResponsibilityCategory.ORGAN
            for port in self.responsibilities
        )
        if organ_count != 9:
            raise ValueError(f"composition must contain nine organs: {organ_count}")


def make_stage1_composition() -> YamichaComposition:
    """Create the complete behavior-free composition used by stage 1."""

    return YamichaComposition(
        core=CoreStub(),
        memory=MemoryStub(),
        state=StateStub(),
        sensation=SensationStub(),
        judgment=JudgmentStub(),
        relationship=RelationshipStub(),
        capability=CapabilityStub(),
        language=LanguageStub(),
        auxiliary_intelligence=AuxiliaryIntelligenceStub(),
        runtime=RuntimeStub(),
        protection_boundary=ProtectionBoundaryStub(),
        external_effect_gate=ExternalEffectGateStub(),
    )
