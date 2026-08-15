"""Core routing for stage-13 conversation-memory candidates."""

from __future__ import annotations

from yamicha.contracts import (
    ConversationMemoryCandidate,
    ConversationMemoryForgetRecord,
    ConversationMemoryForgetRequest,
    ConversationMemoryReview,
)
from yamicha.life.stage10 import Stage10Core

from .memory import Stage13Memory


class Stage13Core(Stage10Core):
    def route_conversation_memory_candidate(
        self,
        candidate: ConversationMemoryCandidate,
    ) -> ConversationMemoryReview:
        memory = self._stage6_memory
        if not isinstance(memory, Stage13Memory):
            raise TypeError("stage-13 conversation memory was not composed")
        return memory.review_conversation_candidate(candidate)

    def route_conversation_memory_forgetting(
        self,
        request: ConversationMemoryForgetRequest,
    ) -> ConversationMemoryForgetRecord:
        memory = self._stage6_memory
        if not isinstance(memory, Stage13Memory):
            raise TypeError("stage-13 conversation memory was not composed")
        return memory.forget_conversation_memory(request)
