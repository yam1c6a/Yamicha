"""Shared declarations for logical responsibility boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from .messages import MessageEnvelope


class ResponsibilityId(StrEnum):
    CORE = "core"
    MEMORY = "memory"
    STATE = "state"
    SENSATION = "sensation"
    JUDGMENT = "judgment"
    RELATIONSHIP = "relationship"
    CAPABILITY = "capability"
    LANGUAGE = "language"
    AUXILIARY_INTELLIGENCE = "auxiliary_intelligence"
    RUNTIME = "runtime"
    PROTECTION_BOUNDARY = "protection_boundary"
    EXTERNAL_EFFECT_GATE = "external_effect_gate"


class ResponsibilityCategory(StrEnum):
    ORGAN = "organ"
    BODY = "body"
    BOUNDARY = "boundary"


@dataclass(frozen=True, slots=True)
class Authority:
    """Authority flags kept separate from descriptive responsibility text."""

    represents_subject: bool = False
    may_propose_direction: bool = False
    may_finalize_direction: bool = False
    may_own_other_responsibility_processing: bool = False
    may_authorize_external_effect: bool = False


@dataclass(frozen=True, slots=True)
class ResponsibilityDefinition:
    identifier: ResponsibilityId
    category: ResponsibilityCategory
    name: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    owned_information: tuple[str, ...]
    prohibitions: tuple[str, ...]
    authority: Authority = Authority()

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("responsibility name must not be empty")
        sections = {
            "inputs": self.inputs,
            "outputs": self.outputs,
            "owned_information": self.owned_information,
            "prohibitions": self.prohibitions,
        }
        missing = [name for name, values in sections.items() if not values]
        if missing:
            raise ValueError(f"responsibility sections must not be empty: {missing}")


class UnimplementedResponsibilityError(NotImplementedError):
    """Raised when a stage-1 stub is invoked before behavior exists."""


@runtime_checkable
class ResponsibilityPort(Protocol):
    """Minimum request/result boundary shared by all responsibilities."""

    definition: ResponsibilityDefinition

    def handle(self, message: MessageEnvelope) -> MessageEnvelope:
        """Accept one traceable request and return one traceable result."""
        ...
