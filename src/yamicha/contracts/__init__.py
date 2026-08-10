"""Technology-independent contracts shared across responsibility boundaries."""

from .messages import MessageEnvelope, VerificationState
from .responsibilities import (
    Authority,
    ResponsibilityCategory,
    ResponsibilityDefinition,
    ResponsibilityId,
    ResponsibilityPort,
    UnimplementedResponsibilityError,
)

__all__ = [
    "Authority",
    "MessageEnvelope",
    "ResponsibilityCategory",
    "ResponsibilityDefinition",
    "ResponsibilityId",
    "ResponsibilityPort",
    "UnimplementedResponsibilityError",
    "VerificationState",
]
