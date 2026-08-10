"""Behavior-free external-effect gate stub for stage 1."""

from yamicha.contracts import MessageEnvelope, UnimplementedResponsibilityError

from .port import EXTERNAL_EFFECT_GATE_DEFINITION


class ExternalEffectGateStub:
    definition = EXTERNAL_EFFECT_GATE_DEFINITION

    def handle(self, message: MessageEnvelope) -> MessageEnvelope:
        raise UnimplementedResponsibilityError(
            "ExternalEffectGate behavior starts after stage 1"
        )
