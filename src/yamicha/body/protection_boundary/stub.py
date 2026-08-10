"""Behavior-free protection-boundary stub for stage 1."""

from yamicha.contracts import MessageEnvelope, UnimplementedResponsibilityError

from .port import PROTECTION_BOUNDARY_DEFINITION


class ProtectionBoundaryStub:
    definition = PROTECTION_BOUNDARY_DEFINITION

    def handle(self, message: MessageEnvelope) -> MessageEnvelope:
        raise UnimplementedResponsibilityError(
            "ProtectionBoundary behavior starts after stage 1"
        )
