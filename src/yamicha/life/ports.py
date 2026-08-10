"""Ports and responsibility declarations for the nine organs."""

from __future__ import annotations

from typing import Protocol

from yamicha.contracts import (
    Authority,
    ResponsibilityCategory,
    ResponsibilityDefinition,
    ResponsibilityId,
    ResponsibilityPort,
)


CORE_DEFINITION = ResponsibilityDefinition(
    identifier=ResponsibilityId.CORE,
    category=ResponsibilityCategory.ORGAN,
    name="Core",
    inputs=("events", "organ results", "judgment proposals", "failure notices"),
    outputs=("organ requests", "finalization or remand", "integrated package"),
    owned_information=("lifecycle correlation", "routing state", "integration state"),
    prohibitions=("creating alternatives", "owning other organs' internal processing", "direct external execution"),
    authority=Authority(may_finalize_direction=True),
)

MEMORY_DEFINITION = ResponsibilityDefinition(
    identifier=ResponsibilityId.MEMORY,
    category=ResponsibilityCategory.ORGAN,
    name="Memory",
    inputs=("memory query", "memory candidate", "retention or forgetting request"),
    outputs=("related memory", "provenance and confidence", "update result"),
    owned_information=("memory items", "meaning", "provenance", "retention state"),
    prohibitions=("final judgment", "substituting current state", "external execution"),
)

STATE_DEFINITION = ResponsibilityDefinition(
    identifier=ResponsibilityId.STATE,
    category=ResponsibilityCategory.ORGAN,
    name="State",
    inputs=("observed state change", "availability fact", "state update candidate"),
    outputs=("timestamped state snapshot", "availability", "constraints"),
    owned_information=("current state", "state transitions", "internal time"),
    prohibitions=("owning long-term memory", "final judgment", "simulated personality"),
)

SENSATION_DEFINITION = ResponsibilityDefinition(
    identifier=ResponsibilityId.SENSATION,
    category=ResponsibilityCategory.ORGAN,
    name="Sensation",
    inputs=("direct external input", "runtime internal event"),
    outputs=("normalized sensory event", "raw input reference", "input quality"),
    owned_information=("directly received raw event", "source", "received time", "input quality"),
    prohibitions=("receiving intelligence output directly", "deciding meaning", "deciding action"),
)

JUDGMENT_DEFINITION = ResponsibilityDefinition(
    identifier=ResponsibilityId.JUDGMENT,
    category=ResponsibilityCategory.ORGAN,
    name="Judgment",
    inputs=("judgment request", "reconsideration request", "materials from organs"),
    outputs=("direction proposal", "comparison reasons", "verification need"),
    owned_information=("alternatives", "proposed direction", "reasons", "uncertainty"),
    prohibitions=("finalizing Yamicha's direction", "direct capability execution", "direct expression generation"),
    authority=Authority(may_propose_direction=True),
)

RELATIONSHIP_DEFINITION = ResponsibilityDefinition(
    identifier=ResponsibilityId.RELATIONSHIP,
    category=ResponsibilityCategory.ORGAN,
    name="Relationship",
    inputs=("human intent through sensation", "relationship candidate", "relationship query"),
    outputs=("relationship context", "agreement validity", "distance and boundary"),
    owned_information=("relationship subject", "agreement", "distance", "continuity context"),
    prohibitions=("owning all records", "external execution", "removing boundaries through familiarity"),
)

CAPABILITY_DEFINITION = ResponsibilityDefinition(
    identifier=ResponsibilityId.CAPABILITY,
    category=ResponsibilityCategory.ORGAN,
    name="Capability",
    inputs=("integrated execution request", "permission context", "tool result"),
    outputs=("feasibility", "expected effect", "execution result", "result unknown"),
    owned_information=("capability contracts", "execution state", "external operation result"),
    prohibitions=("deciding direction", "autonomous execution start", "direct result speech"),
)

LANGUAGE_DEFINITION = ResponsibilityDefinition(
    identifier=ResponsibilityId.LANGUAGE,
    category=ResponsibilityCategory.ORGAN,
    name="Language",
    inputs=("expression request", "integrated package"),
    outputs=("expression result", "inexpressible reason", "evidence mismatch"),
    owned_information=("expression result", "tone", "length", "evidence correspondence"),
    prohibitions=("changing direction", "policy decision", "capability execution", "fabricating facts"),
)

AUXILIARY_INTELLIGENCE_DEFINITION = ResponsibilityDefinition(
    identifier=ResponsibilityId.AUXILIARY_INTELLIGENCE,
    category=ResponsibilityCategory.ORGAN,
    name="AuxiliaryIntelligence",
    inputs=("intelligence request", "permitted context", "external intelligence result"),
    outputs=("candidate material", "provenance", "constraints", "unavailability"),
    owned_information=("intelligence-use context", "query", "raw result", "usage constraints"),
    prohibitions=("acting as subject", "monopolizing judgment", "direct speech, memory, or execution"),
)


ORGAN_DEFINITIONS = (
    CORE_DEFINITION,
    MEMORY_DEFINITION,
    STATE_DEFINITION,
    SENSATION_DEFINITION,
    JUDGMENT_DEFINITION,
    RELATIONSHIP_DEFINITION,
    CAPABILITY_DEFINITION,
    LANGUAGE_DEFINITION,
    AUXILIARY_INTELLIGENCE_DEFINITION,
)


class CorePort(ResponsibilityPort, Protocol):
    pass


class MemoryPort(ResponsibilityPort, Protocol):
    pass


class StatePort(ResponsibilityPort, Protocol):
    pass


class SensationPort(ResponsibilityPort, Protocol):
    pass


class JudgmentPort(ResponsibilityPort, Protocol):
    pass


class RelationshipPort(ResponsibilityPort, Protocol):
    pass


class CapabilityPort(ResponsibilityPort, Protocol):
    pass


class LanguagePort(ResponsibilityPort, Protocol):
    pass


class AuxiliaryIntelligencePort(ResponsibilityPort, Protocol):
    pass
