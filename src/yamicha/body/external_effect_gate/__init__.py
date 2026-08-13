"""External-effect gate declaration and stage-1 stub."""

from .port import EXTERNAL_EFFECT_GATE_DEFINITION, ExternalEffectGatePort
from .stub import ExternalEffectGateStub
from .stage9 import (
    CapabilityGateOutcome,
    RegisteredCapabilityPermissionObserver,
    Stage9ExternalEffectGate,
)

__all__ = [
    "EXTERNAL_EFFECT_GATE_DEFINITION",
    "ExternalEffectGatePort",
    "ExternalEffectGateStub",
    "CapabilityGateOutcome",
    "RegisteredCapabilityPermissionObserver",
    "Stage9ExternalEffectGate",
]
