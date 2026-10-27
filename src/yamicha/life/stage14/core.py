"""Core routing for stage-14 governed memory operations."""

from __future__ import annotations

from yamicha.contracts import (
    ConversationMemoryGovernanceRecord,
    ConversationMemoryGovernanceRequest,
)
from yamicha.life.stage13 import Stage13Core

from .memory import Stage14Memory


class Stage14Core(Stage13Core):
    def route_memory_governance(
        self,
        request: ConversationMemoryGovernanceRequest,
    ) -> ConversationMemoryGovernanceRecord:
        memory = self._stage6_memory
        if not isinstance(memory, Stage14Memory):
            raise TypeError("stage-14 governed memory was not composed")
        return memory.apply_governance(request)
