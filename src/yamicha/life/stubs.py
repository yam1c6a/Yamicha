"""Behavior-free stage-1 stubs for the nine organ ports."""

from __future__ import annotations

from yamicha.contracts import MessageEnvelope, UnimplementedResponsibilityError

from .ports import (
    AUXILIARY_INTELLIGENCE_DEFINITION,
    CAPABILITY_DEFINITION,
    CORE_DEFINITION,
    JUDGMENT_DEFINITION,
    LANGUAGE_DEFINITION,
    MEMORY_DEFINITION,
    RELATIONSHIP_DEFINITION,
    SENSATION_DEFINITION,
    STATE_DEFINITION,
)


class _LifeStub:
    def handle(self, message: MessageEnvelope) -> MessageEnvelope:
        raise UnimplementedResponsibilityError(
            f"{self.definition.name} behavior starts after stage 1"
        )


class CoreStub(_LifeStub):
    definition = CORE_DEFINITION


class MemoryStub(_LifeStub):
    definition = MEMORY_DEFINITION


class StateStub(_LifeStub):
    definition = STATE_DEFINITION


class SensationStub(_LifeStub):
    definition = SENSATION_DEFINITION


class JudgmentStub(_LifeStub):
    definition = JUDGMENT_DEFINITION


class RelationshipStub(_LifeStub):
    definition = RELATIONSHIP_DEFINITION


class CapabilityStub(_LifeStub):
    definition = CAPABILITY_DEFINITION


class LanguageStub(_LifeStub):
    definition = LANGUAGE_DEFINITION


class AuxiliaryIntelligenceStub(_LifeStub):
    definition = AUXILIARY_INTELLIGENCE_DEFINITION
