"""Runtime-side responsibility port."""

from typing import Protocol

from yamicha.contracts import (
    ResponsibilityCategory,
    ResponsibilityDefinition,
    ResponsibilityId,
    ResponsibilityPort,
)


RUNTIME_DEFINITION = ResponsibilityDefinition(
    identifier=ResponsibilityId.RUNTIME,
    category=ResponsibilityCategory.BODY,
    name="Runtime",
    inputs=("startup or shutdown request", "execution opportunity", "delivery request"),
    outputs=("runtime event", "delivery result", "persistence or failure observation"),
    owned_information=("process state", "technical time", "delivery state", "technical logs"),
    prohibitions=("proposing direction", "finalizing direction", "changing organ-owned information"),
)


class RuntimePort(ResponsibilityPort, Protocol):
    pass
