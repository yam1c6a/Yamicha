"""Protection-boundary responsibility port."""

from typing import Protocol

from yamicha.contracts import (
    ResponsibilityCategory,
    ResponsibilityDefinition,
    ResponsibilityId,
    ResponsibilityPort,
)


PROTECTION_BOUNDARY_DEFINITION = ResponsibilityDefinition(
    identifier=ResponsibilityId.PROTECTION_BOUNDARY,
    category=ResponsibilityCategory.BOUNDARY,
    name="ProtectionBoundary",
    inputs=("untrusted input", "permission context", "fixed protection observation"),
    outputs=("validated input", "rejection", "protection transition observation"),
    owned_information=("validation result", "protection state", "fixed-condition evidence"),
    prohibitions=("acting as subject", "creating judgment proposals", "unilateral protection release"),
)


class ProtectionBoundaryPort(ResponsibilityPort, Protocol):
    pass
