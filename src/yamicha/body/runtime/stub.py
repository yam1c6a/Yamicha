"""Behavior-free runtime stub for stage 1."""

from yamicha.contracts import MessageEnvelope, UnimplementedResponsibilityError

from .port import RUNTIME_DEFINITION


class RuntimeStub:
    definition = RUNTIME_DEFINITION

    def handle(self, message: MessageEnvelope) -> MessageEnvelope:
        raise UnimplementedResponsibilityError("Runtime behavior starts after stage 1")
