"""External-effect gate declaration and stage-1 stub."""

from .port import EXTERNAL_EFFECT_GATE_DEFINITION, ExternalEffectGatePort
from .stub import ExternalEffectGateStub

__all__ = [
    "EXTERNAL_EFFECT_GATE_DEFINITION",
    "ExternalEffectGatePort",
    "ExternalEffectGateStub",
]
