"""External-effect gate responsibility port."""

from typing import Protocol

from yamicha.contracts import (
    Authority,
    ResponsibilityCategory,
    ResponsibilityDefinition,
    ResponsibilityId,
    ResponsibilityPort,
)


EXTERNAL_EFFECT_GATE_DEFINITION = ResponsibilityDefinition(
    identifier=ResponsibilityId.EXTERNAL_EFFECT_GATE,
    category=ResponsibilityCategory.BOUNDARY,
    name="ExternalEffectGate",
    inputs=("integrated effect request", "approval evidence", "permission observation"),
    outputs=("execution permission", "rejection", "evidence mismatch"),
    owned_information=("permission-check result", "approval evidence reference", "gate audit"),
    prohibitions=("choosing the external action", "granting permissions", "executing the capability"),
    authority=Authority(may_authorize_external_effect=True),
)


class ExternalEffectGatePort(ResponsibilityPort, Protocol):
    pass
